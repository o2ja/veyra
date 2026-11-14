"""S8 remittances RA-5521 (BRIT, XLSX, exact claim reference) and DN-310 (COPR, payment by deduction) end to end,
against ``expected_reconciliation.json`` ``other_remittances``. SYNTHETIC data. The ground truth numbers the BRIT claim
``CL-2026-000101``; the real numbering is the system's, so that one cell of the XLSX is replaced by the claim's real
reference (the rest of the file is the ground-truth file)."""

import io
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from openpyxl import load_workbook  # type: ignore[import-untyped]  # no stubs shipped

from tests.conftest import DbUrls
from tests.integration.calc_support import (
    SCENARIOS,
    activate_all,
    all_settings,
    claim_and_submit,
    import_sales,
    open_workspace,
    results,
    run,
)
from tests.integration.conftest import Workspace
from tests.reconciliation.support import (
    CSV,
    SPA_NAMES,
    WINDOW,
    XLSX,
    categorise_unmatched,
    claim_refs,
    finalize,
    ingest,
    reconcile,
    upload,
    view,
)
from tests.support import fake_clock, make_app
from tests.testdata import data

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def ws(migrated_db: DbUrls) -> Iterator[Workspace]:
    app: FastAPI = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        w = open_workspace(app, stack)
        all_settings(w)
        activate_all(w, SPA_NAMES)
        import_sales(w, SCENARIOS)
        yield w


@pytest.fixture(scope="module")
def claims(ws: Workspace) -> dict[str, dict[str, str]]:
    """SC-03 (BRIT, INV-10003/1, 448.00) and SC-12 (COPR, INV-10032/1, 60.00), claimed and submitted."""
    res = results(ws, run(ws, **WINDOW)["id"])
    out = {}
    for inv in ("INV-10003", "INV-10032"):
        batch = claim_and_submit(ws, res, [f"{inv}/1"], reference=f"CB-{inv}")
        out[inv] = claim_refs(ws, batch)[inv]
    return out


def test_ra_5521_exact_reference_matches_and_the_unknown_invoice_is_an_unmatched_payment(
    ws: Workspace, claims: dict[str, dict[str, str]]
) -> None:
    claim = claims["INV-10003"]
    assert Decimal(claim["claim_amount"]) == Decimal("448.00")  # ground truth SC-03
    book = load_workbook(io.BytesIO(data("remittance/remittance_BRIT_RA-5521.xlsx")))
    sheet = book.active
    assert sheet["A4"].value == "CL-2026-000101"
    sheet["A4"] = claim["claim_reference"]
    buf = io.BytesIO()
    book.save(buf)
    # Brightline's column "Your ref" is no header synonym: the user confirms a mapping and saves it as a template
    t = ws.api.post(
        "/mapping-templates",
        json={
            "name": "Brightline RA",
            "import_type": "remittance",
            "headers": ["Your ref", "Invoice", "Paid", "Note"],
            "mapping": {
                "claim_reference": "Your ref",
                "invoice_number": "Invoice",
                "amount_paid": "Paid",
                "external_reason_text": "Note",
            },
        },
    )
    assert t.status_code == 201, t.text
    doc = ingest(
        ws,
        upload(ws, "remittance_BRIT_RA-5521.xlsx", buf.getvalue(), XLSX),
        ws.manufacturers["BRIT"],
        doc_type="remittance_advice",
        channel="ach",
        document_number="RA-5521",
        mapping_template_id=t.json()["id"],
        total_amount="568.00",  # hand: 448.00 + 120.00
    )
    assert doc["status"] == "accepted" and Decimal(doc["total_amount"]) == Decimal("568.00")  # 448 + 120
    reconcile(ws, doc["id"])
    rows = view(ws, doc["id"])
    (m1,) = rows[1]["matches"]
    assert (m1["method"], m1["status"]) == ("exact_reference", "confirmed")
    assert [(a["claim_reference"], Decimal(a["allocated_amount"])) for a in m1["allocations"]] == [
        (claim["claim_reference"], Decimal("448.00"))
    ]
    assert rows[2]["state"] == "unmatched" and rows[2]["matches"] == []  # 'inv-10099' has no claim
    assert finalize(ws.api, doc["id"]).status_code == 409  # line 2 is neither matched nor categorised
    assert categorise_unmatched(ws, doc["id"]) == [2]
    r = finalize(ws.api, doc["id"])
    assert r.status_code == 200, r.text
    got = [(f["case_type"], f["reason_code"], Decimal(f["amount"])) for f in r.json()["findings"]]
    assert got == [("unmatched_payment", "unmatched_payment", Decimal("120.00"))]  # BRIT claim paid in full


def test_dn_310_deduction_matches_the_copr_claim_and_leaves_no_variance(
    ws: Workspace, claims: dict[str, dict[str, str]]
) -> None:
    claim = claims["INV-10032"]
    assert Decimal(claim["claim_amount"]) == Decimal("60.00")  # ground truth SC-12
    doc = ingest(
        ws,
        upload(
            ws, "deduction_notice_COPR_DN-310.csv", data("remittance/deduction_notice_COPR_DN-310.csv"), CSV
        ),
        ws.manufacturers["COPR"],
        doc_type="deduction_notice",
        channel="deduction",
        document_number="DN-310",
        total_amount="60.00",
    )
    assert doc["status"] == "accepted" and doc["channel"] == "deduction"
    assert Decimal(doc["total_amount"]) == Decimal("60.00")
    reconcile(ws, doc["id"])
    (row,) = view(ws, doc["id"]).values()
    (m,) = row["matches"]
    assert (m["method"], m["status"]) == ("invoice_line", "confirmed")
    assert [(a["claim_reference"], Decimal(a["allocated_amount"])) for a in m["allocations"]] == [
        (claim["claim_reference"], Decimal("60.00"))
    ]
    r = finalize(ws.api, doc["id"])
    assert r.status_code == 200, r.text
    assert r.json()["findings"] == []
