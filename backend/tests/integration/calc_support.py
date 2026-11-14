"""Helpers for the S5 calculation tests: workspaces with agreements, settings and imported SYNTHETIC files, runs
through the real API and job queue, and the submission of real claim batches."""

import uuid
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests.integration.agreements_support import activate, payload
from tests.integration.conftest import CSV, Workspace, clean_file, import_file, new_workspace, put, run_import
from tests.support import api_client, drain
from tests.testdata import data, truth

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


def claim_and_submit(
    ws: Workspace,
    res: Mapping[str, Mapping[str, Any]],
    keys: Sequence[str],
    reference: str = "CB-VOLT-2026-06",
) -> dict[str, Any]:
    """Claim the eligible lines ``keys`` (``<document>/<line>``) of one manufacturer from the results ``res`` of a
    completed run: create the batch, approve it (four-eyes is off in these workspaces) and submit it, freezing the
    claim lines. The real flow, no stand-in."""
    mfr = {res[k]["manufacturer_id"] for k in keys}
    assert len(mfr) == 1, mfr
    r = ws.api.post(
        "/claim-batches",
        json={"manufacturer_id": mfr.pop(), "result_ids": [res[k]["id"] for k in keys]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert ws.api.post(f"/claim-batches/{bid}/approve").status_code == 200
    r = ws.api.post(
        f"/claim-batches/{bid}/submit",
        json={"submitted_at": "2026-07-05", "external_reference": reference},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def import_sales(ws: Workspace, rel: str = SCENARIOS) -> dict[str, Any]:
    done = import_file(ws, rel, "sales", accept_partial=True)
    assert done["status"] in ("completed", "completed_with_errors"), done
    return done


LATE_RETURN = "CN-5002,"  # SC-09r: the return of INV-10021/1 that arrives after the original was claimed


def import_sales_part(ws: Workspace, *, late: bool) -> dict[str, Any]:
    """The scenario file split in two imports: everything except the late return (``late=False``), or only the late
    return (``late=True``). Claiming between the two reproduces the ground truth's order of events: INV-10021/1 is
    claimed at 250.00 before CN-5002 exists (``claim_batches_fixture.json``, ``expected_calculation.json`` SC-09/09r)."""
    header, *rows = data(SCENARIOS).decode().splitlines(keepends=True)
    keep = [r for r in rows if r.startswith(LATE_RETURN) == late]
    assert keep, late
    done = run_import(
        ws,
        clean_file(ws, "sales_part.csv", (header + "".join(keep)).encode(), CSV),
        "sales",
        accept_partial=True,
    )
    assert done["status"] in ("completed", "completed_with_errors"), done
    return done
