"""Helpers for the S5 calculation tests: workspaces with agreements, settings and imported SYNTHETIC files, runs
through the real API and job queue, and a frozen-claims source built from ``claim_batches_fixture.json``."""

import uuid
from collections.abc import Collection, Mapping, Sequence
from contextlib import ExitStack
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebate.modules.claims import frozen
from rebate_engine.models import PriorClaim, RoundingRule
from tests.integration.agreements_support import activate, payload
from tests.integration.conftest import Workspace, import_file, new_workspace, put
from tests.support import api_client, drain
from tests.testdata import truth

SCENARIOS = "sales/sales_scenarios_layoutA.csv"


def open_workspace(app: FastAPI, stack: ExitStack) -> Workspace:
    """A fresh workspace (master data imported) with four-eyes off: one admin is maker and approver."""
    ws = new_workspace(app, stack.enter_context(api_client(app)))
    etag = ws.api.get("/settings").headers["ETag"]
    r = ws.api.patch("/settings", json={"four_eyes_required": False}, headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    return ws


def activate_all(
    ws: Workspace, names: Sequence[str], edit: Mapping[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    out = {}
    for name in names:
        body = payload(name)
        if edit and name in edit:
            edit[name](body)
        out[name] = activate(ws.api, ws.api, body)
    return out


def settings(ws: Workspace, code: str, **overrides: Any) -> None:
    """PUT the ground-truth manufacturer settings of ``code`` with ``overrides`` applied."""
    gt = next(m for m in truth("master_manufacturers.json") if m["code"] == code)
    body = {k: v for k, v in gt.items() if k not in ("code", "name")} | overrides
    mid = ws.manufacturers[code]
    etag = ws.api.get(f"/manufacturers/{mid}/settings").headers["ETag"]
    r = put(ws.api, f"/manufacturers/{mid}/settings", body, {"If-Match": etag})
    assert r.status_code == 200, r.text


def all_settings(ws: Workspace) -> None:
    for m in truth("master_manufacturers.json"):
        settings(ws, m["code"])


def create_run(ws: Workspace, **body: Any) -> Any:
    return ws.api.post("/calculation-runs", json=body, headers={"Idempotency-Key": str(uuid.uuid4())})


def run(ws: Workspace, **body: Any) -> dict[str, Any]:
    """Create a run, let the worker execute it, return the run resource."""
    r = create_run(ws, **body)
    assert r.status_code == 202, r.text
    drain(ws.app)
    return dict(ws.api.get(f"/calculation-runs/{r.json()['run']['id']}").json())


def results(ws: Workspace, run_id: str, **params: Any) -> dict[str, dict[str, Any]]:
    """The run's results keyed by ``<document>/<line>``."""
    out: dict[str, dict[str, Any]] = {}
    cursor = None
    while True:
        r = ws.api.get(
            f"/calculation-runs/{run_id}/results",
            params={"limit": 200, **params, **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        out |= {f"{x['document_number']}/{x['line_number']}": x for x in body["data"]}
        if not body["page"]["has_more"]:
            return out
        cursor = body["page"]["next_cursor"]


def line_ids(engine: Engine, tenant_id: str) -> dict[str, UUID]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, document_number, line_number FROM sales_document_lines WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
        return {f"{r.document_number}/{r.line_number}": r.id for r in rows}


class FixtureClaims:
    """``FrozenClaimSource`` standing in for S7's ``claim_lines`` reader, built from the ground-truth batch
    CB-VOLT-2026-06 (``claim_batches_fixture.json``) and the scenario quantities/unit amounts of
    ``expected_calculation.json``. ``amounts`` overrides a line's frozen claim amount (proposal tests)."""

    def __init__(
        self,
        ids: Mapping[str, UUID],
        agreement_id: str,
        version_id: str,
        manufacturer_id: UUID,
        amounts: Mapping[str, str] | None = None,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        calc = {
            f"{s['invoice_no']}/{s['line_no']}": s for s in truth("expected_calculation.json")["scenarios"]
        }
        batch = {f"{x['invoice_no']}/{x['line_no']}": x for x in truth("claim_batches_fixture.json")["lines"]}
        rule = RoundingRule(mode="half_up", scale=2, timing="per_line")  # SPA-VOLT-2026-884 rounding
        self.claims: dict[UUID, tuple[str, PriorClaim]] = {}
        for key, line in batch.items():
            amount = (amounts or {}).get(key, line["claim_amount"])
            sc = calc[key]
            self.claims[ids[key]] = (
                key,
                PriorClaim(
                    agreement_id=agreement_id,
                    agreement_version_id=version_id,
                    claim_uom="EA",
                    quantity=Decimal(sc["eligible_quantity"]),
                    unit_amount=Decimal(sc["claim_unit_amount"]),
                    claim_amount=Decimal(amount),
                    rounding=rule,
                ),
            )
        for key, amount in (extra or {}).items():  # claims on lines the batch does not contain
            self.claims[ids[key]] = (
                key,
                PriorClaim(
                    agreement_id=agreement_id,
                    agreement_version_id=version_id,
                    claim_uom="EA",
                    quantity=Decimal(1),
                    unit_amount=Decimal(amount),
                    claim_amount=Decimal(amount),
                    rounding=rule,
                ),
            )
        self.manufacturer_id = manufacturer_id

    def prior_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, PriorClaim]:
        return {i: self.claims[i][1] for i in line_ids if i in self.claims}

    def consumption(
        self, db: Session, tenant_id: UUID, exclude_line_ids: Collection[UUID]
    ) -> Mapping[tuple[str, str], Decimal]:
        return {}

    def frozen_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, frozen.FrozenClaim]:
        return {
            i: frozen.FrozenClaim(
                uuid.uuid5(uuid.NAMESPACE_URL, str(i)),
                i,
                self.manufacturer_id,
                self.claims[i][1].claim_amount,
            )
            for i in line_ids
            if i in self.claims
        }


def import_sales(ws: Workspace, rel: str = SCENARIOS) -> dict[str, Any]:
    done = import_file(ws, rel, "sales", accept_partial=True)
    assert done["status"] in ("completed", "completed_with_errors"), done
    return done
