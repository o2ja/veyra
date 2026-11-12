"""Matching part of testdata/generated/ground_truth/expected_reconciliation.json (CM-7781, RA-5521, DN-310)."""

from dataclasses import replace
from decimal import Decimal

from rebate_matching.engine import reconcile
from rebate_matching.models import ClaimLine, Method, Status
from tests.matching import data

EXP = data.expected_reconciliation()


def test_cm_7781_matching() -> None:
    res = reconcile(data.cm_7781_lines(), data.volt_claims())
    by_line = {m.remittance_line_id: m for m in res.matches}
    expected = {e["remittance_line"]: e for e in EXP["expected"] if "remittance_line" in e}
    assert set(expected) == {1, 2, 3, 4, 5, 6}

    for no, e in expected.items():
        m = by_line.get(str(no))
        if e["status"] == "unmatched":
            assert m is None, f"line {no} must stay unmatched"
            continue
        assert m is not None, f"line {no}"
        assert m.method.value == e["method"]
        assert m.status.value == e["status"]
        refs = [e["claim_reference"]] if "claim_reference" in e else e.get("claim_references")
        if refs:
            assert sorted({a.claim_line_id for a in m.allocations}) == sorted(refs)

    # allocations given by the ground truth
    assert [(a.claim_line_id, a.adjustment_ref, a.amount) for a in by_line["4"].allocations] == [
        ("CL-2026-000004", None, Decimal("250.00")),
        ("CL-2026-000004", "ADJ-2026-0001", Decimal("-50.00")),
    ]
    assert [a.amount for a in by_line["5"].allocations] == [Decimal(x) for x in expected[5]["allocations"]]
    # hand derivation: 1:1 matches allocate what was paid (2050 of 2200, 0 of 2080)
    assert by_line["1"].allocations[0].amount == Decimal("2050.00")
    assert by_line["3"].allocations[0].amount == Decimal("0.00")
    # exact tiers may auto-confirm; the suggestion never
    assert [by_line[k].auto_confirm for k in "12345"] == [True, True, True, True, False]
    # CL-2026-000007 is not on the remittance; line 6 unmatched
    assert res.unmatched_claim_ids == ("CL-2026-000007",)
    assert res.unmatched_remittance_ids == ("6",)
    assert not res.notes


def test_ra_5521_matching() -> None:
    claims = [
        ClaimLine("c101", "BRIT", "CL-2026-000101", "INV-10003", 1, Decimal("448.00")),
    ]
    res = reconcile(data.ra_5521_lines(), claims)
    assert [(m.remittance_line_id, m.method, m.status) for m in res.matches] == [
        ("1", Method.EXACT_REFERENCE, Status.CONFIRMED)
    ]
    assert res.unmatched_remittance_ids == ("2",)  # inv-10099: no claim in the fixture
    assert EXP["other_remittances"]["RA-5521"].startswith("line1 matches CL-2026-000101 by exact_reference")


def test_ra_5521_second_line_matches_by_invoice_line_after_normalising() -> None:
    """Hand derivation: if a claim for INV-10099 line 1 existed, 'inv-10099' must map to it (T2)."""
    claims = [
        ClaimLine("c101", "BRIT", "CL-2026-000101", "INV-10003", 1, Decimal("448.00")),
        ClaimLine("c102", "BRIT", "CL-2026-000102", "INV-10099", 1, Decimal("120.00")),
    ]
    rems = [r if r.id == "1" else replace(r, invoice_line=1) for r in data.ra_5521_lines()]
    res = reconcile(rems, claims)
    assert {m.remittance_line_id: m.method for m in res.matches} == {
        "1": Method.EXACT_REFERENCE,
        "2": Method.INVOICE_LINE,
    }


def test_dn_310_deduction_matched_like_any_remittance() -> None:
    claims = [
        ClaimLine("c1", "COPR", "CL-COPR-1", "INV-10032", 1, Decimal("60.00"))
    ]  # claim of 60.00 (ground truth)
    res = reconcile(data.dn_310_lines(), claims)
    (m,) = res.matches
    assert (m.method, m.status, m.auto_confirm) == (Method.INVOICE_LINE, Status.CONFIRMED, True)
    assert m.allocated == Decimal("60.00")  # no variance: paid == claimed
    assert EXP["other_remittances"]["DN-310"].endswith("no variance")
