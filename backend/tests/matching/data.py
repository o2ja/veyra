"""Inputs built from the SYNTHETIC remittance files and claim_batches_fixture.json (stdlib only).

Expected values are NOT computed here: they come from expected_reconciliation.json or hand derivation.
"""

import csv
import zipfile
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

from rebate_matching.models import Adjustment, ClaimLine, RemittanceLine
from tests.engine_golden.testdata_inputs import ground_truth, testdata_root

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def expected_reconciliation() -> dict[str, Any]:
    out: dict[str, Any] = ground_truth("expected_reconciliation.json")
    return out


def volt_claims() -> list[ClaimLine]:
    batch = ground_truth("claim_batches_fixture.json")
    adj: dict[str, list[Adjustment]] = {}
    for a in batch["adjustments"]:
        if a["included_in_batch"]:
            adj.setdefault(a["claim_reference"], []).append(
                Adjustment(a["adjustment_ref"], Decimal(a["amount"]))
            )
    return [
        ClaimLine(
            id=ln["claim_reference"],
            manufacturer="VOLT",
            claim_reference=ln["claim_reference"],
            invoice_no=ln["invoice_no"],
            invoice_line=ln["line_no"],
            amount=Decimal(ln["claim_amount"]),
            adjustments=tuple(adj.get(ln["claim_reference"], ())),
        )
        for ln in batch["lines"]
    ]


def cm_7781_lines() -> list[RemittanceLine]:
    path = testdata_root() / "remittance" / "credit_memo_VOLT_CM-7781.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [
        RemittanceLine(
            id=r["line"],
            manufacturer="VOLT",
            amount_paid=Decimal(r["amount_paid"]),
            invoice_no=r["invoice_no"] or None,
            invoice_line=int(r["invoice_line"]) if r["invoice_line"] else None,
            sku=r["part_no"] or None,
            quantity=Decimal(r["qty"]) if r["qty"] else None,
            reason_code=r["reason_code"] or None,
        )
        for r in rows
    ]


def dn_310_lines() -> list[RemittanceLine]:
    path = testdata_root() / "remittance" / "deduction_notice_COPR_DN-310.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [
        RemittanceLine(
            id=r["notice"],
            manufacturer="COPR",
            amount_paid=Decimal(r["deduction_amount"]),
            invoice_no=r["invoice_no"],
            invoice_line=int(r["invoice_line"]),
        )
        for r in rows
    ]


def ra_5521_lines() -> list[RemittanceLine]:
    """Rows of the first sheet of the XLSX (inline strings), header row 'Your ref | Invoice | Paid | Note'."""
    with zipfile.ZipFile(testdata_root() / "remittance" / "remittance_BRIT_RA-5521.xlsx") as z:
        root = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))  # noqa: S314 - synthetic repo file
    rows: dict[int, dict[str, str]] = {}
    for c in root.iterfind(".//m:c", _NS):
        ref = c.attrib["r"]
        text = "".join(t.text or "" for t in c.iterfind(".//m:t", _NS))
        rows.setdefault(int(ref[1:]), {})[ref[0]] = text
    out = []
    for n in sorted(k for k in rows if k >= 4):
        r = rows[n]
        out.append(
            RemittanceLine(
                id=str(n - 3),
                manufacturer="BRIT",
                amount_paid=Decimal(r["C"]),
                claim_reference=r.get("A") or None,
                invoice_no=r.get("B") or None,
            )
        )
    return out
