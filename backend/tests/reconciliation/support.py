"""Helpers for the S8 reconciliation tests: the ground-truth claim story built through the real API (imports, agreements,
calculation runs, claim batches, an approved return adjustment), and remittance uploads. SYNTHETIC data only."""

import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from decimal import Decimal
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


def categorise_unmatched(ws: Workspace, remittance_id: str) -> list[int]:
    """A person categorises every line that has no match as an unmatched payment; returns their line numbers."""
    done = []
    for n, row in sorted(view(ws, remittance_id).items()):
        if row["state"] == "unmatched":
            r = ws.api.post(
                f"/remittance-lines/{row['line']['id']}/categorise", json={"category": "unmatched_payment"}
            )
            assert r.status_code == 200, r.text
            done.append(n)
    return done


def finalize(api: Api, remittance_id: str) -> Any:
    return api.post(f"/remittances/{remittance_id}/finalize", headers=key())


def build_story(app: FastAPI, stack: ExitStack) -> Story:
    """The ground-truth story of ``CB-VOLT-2026-06``: seven lines claimed and submitted (2026-07-05), the credit note
    CN-5002 arrives, its -50.00 return reversal (ADJ-2026-0001) is approved and rides in a second submitted batch
    together with INV-10020/1 (312.50). The ground-truth JSON puts ADJ-2026-0001 in the SAME batch as its claim line,
    an ordering the real flow cannot produce (an adjustment needs a frozen original and is included only when a
    batch is created, claims/batches.py), so the story differs from it on purpose: by the owner rule (2026-11-13) an
    adjustment belongs to the batch that carries it, so B1 = 10,410.00 and B2 = 312.50 - 50.00 = 262.50. VOLT's expected payment lag is 3 days (so on 2026-07-10 the first batch, submitted 07-05, is overdue and the carrier
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


# ---- ground-truth checks shared by the CSV and the PDF flows ---------------------------------------------------


def ref_of(story: Story, ground_truth_reference: str) -> str:
    """The ground truth numbers its claim references CL-2026-000001..7 in batch order; the real numbering is the
    system's, so translate through the invoice number."""
    invoice = next(
        x["invoice_no"] for x in FIXTURE["lines"] if x["claim_reference"] == ground_truth_reference
    )
    return str(story.lines[invoice]["claim_reference"])


def amounts(match: dict[str, Any]) -> list[Decimal]:
    return [Decimal(a["allocated_amount"]) for a in match["allocations"]]


def check_matching(story: Story, rows: dict[int, dict[str, Any]]) -> None:
    """The matching run of CM-7781 equals ``expected_reconciliation.json`` (method, status, claim, allocations)."""
    assert sorted(rows) == [1, 2, 3, 4, 5, 6]
    for exp in EXPECTED["expected"]:
        if exp.get("not_on_remittance"):
            continue
        n = exp["remittance_line"]
        got = rows[n]
        if exp["status"] == "unmatched":
            assert got["state"] == "unmatched" and got["matches"] == [], n
            continue
        (m,) = got["matches"]
        assert (m["method"], m["status"]) == (exp["method"], exp["status"]), n
        if "claim_reference" in exp and "allocations" not in exp:
            assert [a["claim_reference"] for a in m["allocations"]] == [
                ref_of(story, exp["claim_reference"])
            ], n
        if n == 4:
            # the adjustment rides in another batch: it is not split off this payment, all 200.00 -> CL-4
            assert amounts(m) == [Decimal("200.00")]
            assert [a["claim_reference"] for a in m["allocations"]] == [ref_of(story, "CL-2026-000004")]
            assert m["allocations"][0]["claim_adjustment_id"] is None
        if n == 5:  # GL-V2: unique subset 1000.00 + 1100.00, needs a person
            assert sorted(amounts(m)) == sorted(Decimal(a) for a in exp["allocations"])
            assert sorted(a["claim_reference"] for a in m["allocations"]) == sorted(
                ref_of(story, c) for c in exp["claim_references"]
            )
            assert got["state"] == "suggested"
        else:
            assert got["state"] == "matched"
    # per-line paid amounts of the 1:1 matches (hand: the amounts of the file)
    assert amounts(rows[1]["matches"][0]) == [Decimal("2050.00")]
    assert amounts(rows[2]["matches"][0]) == [Decimal("3120.00")]
    assert amounts(rows[3]["matches"][0]) == [Decimal("0.00")]


def check_findings(story: Story, body: dict[str, Any]) -> None:
    """The finalised CM-7781 yields the ground truth's variance cases and nothing else (VOLT lag 3 days, today
    2026-07-10: the batch submitted 07-05 is overdue, the carrier batch submitted 07-09 is not)."""
    by_invoice = {v["id"]: inv for inv, v in story.lines.items()}
    got = sorted(
        (f["case_type"], f["reason_code"], Decimal(f["amount"]), by_invoice.get(f["claim_line_id"]) or "")
        for f in body["findings"]
    )
    expected = sorted(
        [
            ("short_paid", "rejected_price_mismatch", Decimal("150.00"), "INV-10001"),  # GL-V1
            ("rejected", "rejected_quantity_exceeds_authorization", Decimal("2080.00"), "INV-10011"),
            # CL-4: frozen 250.00, paid 200.00 -> 50.00 short, no code (owner rule 2026-11-13; the GT JSON's "no variance"
            # needs the -50.00 in CL-4's own batch). Cross-check: 10,410.00 + 262.50 (B2, not yet due) = 10,672.50;
            # cash on matched lines 7,470.00; 10,672.50 - 7,470.00 = 3,202.50 = 150 + 2080 + 50 + 660 + 262.50.
            ("short_paid", "short_paid_unexplained", Decimal("50.00"), "INV-10021"),
            ("unmatched_payment", "unmatched_payment", Decimal("75.00"), ""),
            ("unpaid", "unpaid_no_remittance", Decimal("660.00"), "INV-10060"),
        ]
    )
    assert got == expected
    for exp in EXPECTED["expected"]:  # every ground-truth variance is among the findings
        v = exp.get("variance")
        if v:  # (the GT's null variance for line 4 is the same-batch ordering; see the story docstring)
            assert (v["case_type"], v["reason_code"], Decimal(v["amount"])) in {
                (c, r, a) for c, r, a, _ in got
            }
    short = next(f for f in body["findings"] if f["case_type"] == "short_paid")
    assert (Decimal(short["claimed"]), Decimal(short["paid"]), short["external_reason_code"]) == (
        Decimal("2200.00"),
        Decimal("2050.00"),
        "E12",
    )
