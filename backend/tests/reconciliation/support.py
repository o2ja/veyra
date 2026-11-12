"""Helpers for the S8 reconciliation tests: the ground-truth claim story built through the real API (imports, agreements,
calculation runs, claim batches, an approved return adjustment), and remittance uploads. SYNTHETIC data only."""

import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from tests.integration.calc_support import (
    activate_all,
    all_settings,
    claim_and_submit,
    import_sales_part,
    open_workspace,
    results,
    run,
    settings,
)
from tests.integration.conftest import Workspace, put
from tests.support import Api, drain
from tests.testdata import truth

SPA_NAMES = ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901", "SPA-BRIT-2026-031", "SPA-COPR-2026-107")
WINDOW = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
FIXTURE = truth("claim_batches_fixture.json")
FIX7 = [f"{x['invoice_no']}/{x['line_no']}" for x in FIXTURE["lines"]]
EXPECTED = truth("expected_reconciliation.json")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"
PDF = "application/pdf"


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def listing(api: Api, path: str, **params: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        r = api.get(path, params={"limit": 200, **params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        out += r.json()["data"]
        if not r.json()["page"]["has_more"]:
            return out
        cursor = r.json()["page"]["next_cursor"]


def pool(api: Api) -> dict[str, dict[str, Any]]:
    return {f"{x['document_number']}/{x['line_number']}": x for x in listing(api, "/missed-claims")}


def claim_refs(ws: Workspace, batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """invoice number -> claim line (id, claim_reference, ...) of a batch."""
    return {x["document_number"]: x for x in listing(ws.api, f"/claim-batches/{batch['id']}/lines")}


@dataclass
class Story:
    ws: Workspace
    mfr: dict[str, str]
    lines: dict[
        str, dict[str, Any]
    ]  # invoice number -> claim line, VOLT batch CB-VOLT-2026-06 + the carrier line
    adjustment: dict[str, Any]


def upload(ws: Workspace, name: str, body: bytes, mime: str, kind: str = "remittance") -> str:
    r = ws.api.client.post(
        "/api/v1/files",
        files={"file": (name, body, mime)},
        data={"kind": kind},
        headers={"Origin": "https://testserver", "X-CSRF-Token": ws.api.csrf()},
    )
    assert r.status_code == 201, r.text
    drain(ws.app)
    f = ws.api.get(f"/files/{r.json()['id']}").json()
    assert f["scan_status"] == "clean", f
    return str(f["id"])


def create(ws: Workspace, file_id: str, manufacturer: str, **body: Any) -> Any:
    payload = {
        "file_id": file_id,
        "manufacturer_id": manufacturer,
        "doc_type": "credit_memo",
        "channel": "credit_memo",
        **body,
    }
    return ws.api.post("/remittances", json=payload, headers=key())


def ingest(ws: Workspace, file_id: str, manufacturer: str, **body: Any) -> dict[str, Any]:
    """Create a remittance and let the worker run the ingest job; returns the remittance resource."""
    r = create(ws, file_id, manufacturer, **body)
    assert r.status_code == 202, r.text
    drain(ws.app)
    got = ws.api.get(f"/remittances/{r.json()['remittance']['id']}")
    assert got.status_code == 200, got.text
    return dict(got.json())


def reconcile(ws: Workspace, remittance_id: str) -> None:
    r = ws.api.post(f"/remittances/{remittance_id}/reconcile")
    assert r.status_code == 202, r.text
    drain(ws.app)


def view(ws: Workspace, remittance_id: str) -> dict[int, dict[str, Any]]:
    """Reconciliation view keyed by line number."""
    out = listing_of(ws, f"/remittances/{remittance_id}/reconciliation")
    return {x["line"]["line_number"]: x for x in out}


def listing_of(ws: Workspace, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        r = ws.api.get(path, params={"limit": 200, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        out += r.json()["data"]
        if not r.json()["page"]["has_more"]:
            return out
        cursor = r.json()["page"]["next_cursor"]


def finalize(api: Api, remittance_id: str) -> Any:
    return api.post(f"/remittances/{remittance_id}/finalize", headers=key())


def build_story(app: FastAPI, stack: ExitStack) -> Story:
    """The ground-truth story of ``CB-VOLT-2026-06``: seven lines claimed and submitted (2026-07-05), the credit note
    CN-5002 arrives, its -50.00 return reversal (ADJ-2026-0001) is approved and rides in a second submitted batch
    together with INV-10020/1 (312.50); VOLT's expected payment lag is 3 days (so on 2026-07-10 the first batch, submitted 07-05, is overdue and the carrier
    batch, submitted 07-09, is not); E07/E12 are mapped (ground truth)."""
    ws = open_workspace(app, stack)
    all_settings(ws)
    settings(ws, "VOLT", payment_lag_days_expected=3)
    volt = ws.manufacturers["VOLT"]
    mappings = [{"external_code": c, "reason_code": r} for c, r in EXPECTED["reason_code_mappings"].items()]
    assert (
        put(ws.api, f"/manufacturers/{volt}/reason-code-mappings", {"mappings": mappings}).status_code == 200
    )
    activate_all(ws, SPA_NAMES)
    import_sales_part(ws, late=False)
    first = run(ws, **WINDOW)
    batch1 = claim_and_submit(ws, results(ws, first["id"]), FIX7)
    lines = claim_refs(ws, batch1)
    import_sales_part(ws, late=True)
    second = run(ws, **WINDOW)
    r = ws.api.post(f"/calculation-runs/{second['id']}/propose-adjustments")
    assert r.status_code == 200, r.text
    (adj,) = r.json()["proposed"]
    assert ws.api.post(f"/claim-adjustments/{adj['id']}/approve").status_code == 200
    carrier = pool(ws.api)["INV-10020/1"]
    b = ws.api.post(
        "/claim-batches",
        json={"manufacturer_id": volt, "result_ids": [carrier["id"]], "include_adjustment_ids": [adj["id"]]},
        headers=key(),
    )
    assert b.status_code == 201, b.text
    assert ws.api.post(f"/claim-batches/{b.json()['id']}/approve").status_code == 200
    s = ws.api.post(
        f"/claim-batches/{b.json()['id']}/submit",
        json={"submitted_at": "2026-07-09", "external_reference": "VOLT-ADJ-1"},
        headers=key(),
    )
    assert s.status_code == 200, s.text
    lines |= claim_refs(ws, b.json())
    return Story(ws, dict(ws.manufacturers), lines, adj)
