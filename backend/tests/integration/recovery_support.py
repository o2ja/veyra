"""Helpers for the S9 recovery tests: the ground-truth story (claimed batch CB-VOLT-2026-06 through the real API),
remittance documents seeded in the S8 tables with the same SQL the isolation seed uses, and cases opened through the
public ``recovery.open_cases`` in a tenant transaction. No stand-ins: every row passes the real triggers."""

import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.modules import recovery
from rebate.modules.masterdata import Who
from rebate.modules.reconciliation import FinalisedRemittance, Finding
from tests.integration.calc_support import (
    activate_all,
    all_settings,
    claim_and_submit,
    import_sales_part,
    open_workspace,
    results,
    run,
)
from tests.integration.conftest import Workspace
from tests.support import Api
from tests.testdata import truth

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
SPA_NAMES = ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901", "SPA-BRIT-2026-031", "SPA-COPR-2026-107")
WINDOW = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
FIXTURE = truth("claim_batches_fixture.json")
LINES = FIXTURE["lines"]  # CL-2026-000001 .. 000007 (ground truth)
FIX7 = [f"{x['invoice_no']}/{x['line_no']}" for x in LINES]
D = Decimal


@dataclass
class Story:
    ws: Workspace
    tenant: UUID
    user: UUID
    manufacturer: UUID
    claim_lines: dict[
        str, UUID
    ]  # invoice number -> claim line id (references are numbered by the batch order)

    def who(self) -> Who:
        return Who(self.tenant, self.user, {})


def build_story(app: FastAPI, stack: ExitStack) -> Story:
    """Workspace with agreements and VOLT settings (dispute window 60 days from the remittance date), the seven
    ground-truth lines claimed and submitted, four-eyes off (one admin works alone)."""
    ws = open_workspace(app, stack)
    all_settings(ws)
    activate_all(ws, SPA_NAMES)
    import_sales_part(ws, late=False)
    first = run(ws, **WINDOW)
    batch = claim_and_submit(ws, results(ws, first["id"]), FIX7)
    out: dict[str, UUID] = {}
    cursor = None
    while True:
        r = ws.api.get(
            f"/claim-batches/{batch['id']}/lines",
            params={"limit": 200, **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200, r.text
        for x in r.json()["data"]:
            gt = next(g for g in LINES if g["invoice_no"] == x["document_number"])
            assert D(x["claim_amount"]) == D(gt["claim_amount"]), (x["document_number"], x["claim_amount"])
            out[x["document_number"]] = UUID(x["id"])
        if not r.json()["page"]["has_more"]:
            break
        cursor = r.json()["page"]["next_cursor"]
    assert sorted(out) == sorted(x["invoice_no"] for x in LINES)
    return Story(ws, UUID(ws.tenant_id), UUID(ws.user_id), UUID(ws.manufacturers["VOLT"]), out)


def seed_remittance(
    engine: Engine,
    story: Story,
    number: str,
    document_date: date,
    amounts: list[Decimal],
    codes: list[str | None],
) -> tuple[UUID, list[UUID]]:
    """An accepted credit-memo remittance with one line per amount (inserted as the real flow does: staging rows,
    lines, then acceptance, which the database checks against the total)."""
    doc, file_id = uuid.uuid4(), uuid.uuid4()
    lines = [uuid.uuid4() for _ in amounts]
    p = {"t": story.tenant, "u": story.user, "m": story.manufacturer, "doc": doc, "f": file_id, "n": number,
         "d": document_date, "total": sum(amounts, D(0))}  # fmt: skip
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO file_objects (tenant_id, id, kind, original_name, storage_key, size_bytes, sha256, "
                "detected_mime, uploaded_by, scan_status, scanned_at) VALUES (:t, :f, 'remittance', 'rem.csv', "
                "'quarantine/' || :t || '/' || :f, 10, :h, 'text/csv', :u, 'clean', now())"
            ),
            {**p, "h": file_id.hex * 2},
        )
        c.execute(
            text(
                "INSERT INTO remittance_documents (tenant_id, id, manufacturer_id, file_id, doc_type, channel, "
                "document_number, document_date, total_amount, created_by) VALUES (:t, :doc, :m, :f, 'credit_memo', "
                "'credit_memo', :n, :d, :total, :u)"
            ),
            p,
        )
        for i, (line, amount, code) in enumerate(zip(lines, amounts, codes, strict=True), start=1):
            q = {"t": story.tenant, "doc": doc, "line": line, "i": i, "a": amount, "c": code}
            c.execute(
                text(
                    "INSERT INTO remittance_extraction_staging (tenant_id, id, remittance_document_id, line_number, "
                    "amount_paid) VALUES (:t, gen_random_uuid(), :doc, :i, :a)"
                ),
                q,
            )
            c.execute(
                text(
                    "INSERT INTO remittance_lines (tenant_id, id, remittance_document_id, line_number, amount_paid, "
                    "external_reason_code) VALUES (:t, :line, :doc, :i, :a, :c)"
                ),
                q,
            )
        c.execute(
            text(
                "UPDATE remittance_documents SET status = 'accepted', accepted_by = :u, accepted_at = now() "
                "WHERE tenant_id = :t AND id = :doc"
            ),
            p,
        )
    return doc, lines


def confirm_payment(
    engine: Engine, story: Story, remittance_line: UUID, adjustment_id: str, amount: Decimal
) -> None:
    """A user-confirmed allocation of a remittance line to a reclaim adjustment (what S8 persists on confirm)."""
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO reconciliation_matches (tenant_id, id, match_id, remittance_line_id, "
                "claim_adjustment_id, allocated_amount, method, evidence, status, confirmed_by, confirmed_at) "
                "VALUES (:t, :id, :id, :rl, :adj, :a, 'manual', '{}', 'confirmed', :u, now())"
            ),
            {"t": story.tenant, "id": uuid.uuid4(), "rl": remittance_line, "adj": adjustment_id, "a": amount,
             "u": story.user},
        )  # fmt: skip


def open_cases(engine: Engine, story: Story, rem: FinalisedRemittance) -> list[Any]:
    with transaction(engine, story.tenant) as db:
        return [c.id for c in recovery.open_cases(db, story.who(), rem)]


def finding(case_type: str, amount: str, reason: str, **kw: Any) -> Finding:
    return Finding(
        case_type=case_type, amount=D(amount), reason_code=reason, **{"external_reason_code": None, **kw}
    )


def remittance(
    doc: UUID, manufacturer: UUID, doc_date: date, findings: tuple[Finding, ...]
) -> FinalisedRemittance:
    """What reconciliation's finalise hands to the recovery hook."""
    return FinalisedRemittance(doc, manufacturer, None, doc_date, None, "credit_memo", findings)


def case_by(api: Api, case_id: UUID | str) -> dict[str, Any]:
    r = api.get(f"/cases/{case_id}")
    assert r.status_code == 200, r.text
    return {**r.json(), "etag": r.headers["ETag"]}


def act(
    api: Api, method: str, path: str, case_id: UUID | str, body: Any = None, etag: str | None = None
) -> Any:
    """POST/PATCH on a case with the current ETag (or ``etag``)."""
    h = {
        "If-Match": etag or case_by(api, case_id)["etag"],
        "Idempotency-Key": str(uuid.uuid4()),
    }  # write-off needs it
    return getattr(api, method)(path, json=body, headers=h)
