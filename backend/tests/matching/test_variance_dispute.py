"""§10.3 / §10.4 against expected_reconciliation.json (CM-7781) and hand-derived cases."""

from datetime import date
from decimal import Decimal

import pytest

from rebate_matching.dispute import DisputeFacts, assess_dispute
from rebate_matching.engine import confirm_suggestion, manual_adjustment_match, manual_match, reconcile
from rebate_matching.models import Adjustment, ClaimLine, Method, RemittanceLine, Status
from rebate_matching.variance import (
    classify_claim_line,
    classify_unmatched_payment,
    paid_for,
)
from tests.matching import data

EXP = data.expected_reconciliation()
D = Decimal


def test_cm_7781_variances_match_ground_truth() -> None:
    rems = data.cm_7781_lines()
    claims = data.volt_claims()
    res = reconcile(rems, claims)
    rmap = {r.id: r for r in rems}
    mappings = EXP["reason_code_mappings"]
    # only confirmed matches count as paid (line 5 is a suggestion until a human confirms it)
    confirmed = [m for m in res.matches if m.status is Status.CONFIRMED]
    got = {
        c.claim_reference: classify_claim_line(
            c,
            confirmed,
            rmap,
            mappings,
            as_of=date(2026, 8, 20),
            submitted_at=date(2026, 7, 5),
            payment_lag_days=30,
        )
        for c in claims
    }
    for e in EXP["expected"]:
        ref = e.get("claim_reference")
        if ref is None or "variance" not in e:
            continue
        v = e["variance"]
        case = got[ref]
        if v is None:
            assert case is None, ref
        else:
            assert case is not None, ref
            assert (case.case_type, case.amount, case.reason_code) == (
                v["case_type"],
                D(v["amount"]),
                v["reason_code"],
            ), ref
    # L4 (hand derivation, ground-truth ordering: ADJ in CL-4's own batch): the payment 200.00 splits into
    # 250.00 (CL-4) + (-50.00) (ADJ); CL-4 claimed 250.00, paid 250.00 -> no case. Batch total 10,410.00 - 50.00 =
    # 10,360.00 (= GT batch_total).
    assert got["CL-2026-000004"] is None
    # GL-V2: L5 (2,100.00) is a T5 suggestion over CL-5 (1,000.00) + CL-6 (1,100.00); once a user confirms it,
    # neither line has a variance
    sugg = [m for m in res.matches if m.status is Status.SUGGESTED and m.remittance_line_id == "5"]
    assert len(sugg) == 1
    after = [*confirmed, confirm_suggestion(sugg[0])]
    for ref in ("CL-2026-000005", "CL-2026-000006"):
        claim = next(c for c in claims if c.claim_reference == ref)
        assert classify_claim_line(claim, after, rmap, mappings) is None, ref
    # unmatched payment (line 6): 75.00
    case6 = classify_unmatched_payment(rmap["6"], res.matches, categorised=True)
    assert case6 is not None and (case6.case_type, case6.amount, case6.reason_code) == (
        "unmatched_payment",
        D("75.00"),
        "unmatched_payment",
    )
    assert classify_unmatched_payment(rmap["1"], res.matches) is None


def test_unpaid_only_after_lag() -> None:
    cl7 = next(c for c in data.volt_claims() if c.claim_reference == "CL-2026-000007")
    sub = date(2026, 7, 5)
    assert (
        classify_claim_line(cl7, [], {}, {}, as_of=date(2026, 8, 4), submitted_at=sub, payment_lag_days=30)
        is None
    )
    case = classify_claim_line(cl7, [], {}, {}, as_of=date(2026, 8, 5), submitted_at=sub, payment_lag_days=30)
    assert case is not None and (case.case_type, case.amount, case.reason_code) == (
        "unpaid",
        D("660.00"),
        "unpaid_no_remittance",
    )
    assert classify_claim_line(cl7, [], {}, {}) is None  # lag not configured


def _single(claimed: str, paid: str, code: str | None = None):  # type: ignore[no-untyped-def]
    c = ClaimLine("c", "M", "CL-c", "INV-1", 1, D(claimed))
    r = RemittanceLine("r", "M", D(paid), invoice_no="INV-1", invoice_line=1, reason_code=code)
    res = reconcile([r], [c])
    return classify_claim_line(c, res.matches, {"r": r}, {"E12": "rejected_price_mismatch"})


@pytest.mark.parametrize(
    ("claimed", "paid", "code", "expected"),
    [
        ("100.00", "100.00", None, None),
        ("100.00", "99.99", None, None),  # |diff| = tol
        ("100.00", "99.98", None, ("short_paid", "0.02", "short_paid_unexplained")),
        ("100.00", "50.00", "E12", ("short_paid", "50.00", "rejected_price_mismatch")),
        ("100.00", "50.00", "ZZ", ("short_paid", "50.00", "unknown")),
        ("100.00", "0.00", "ZZ", ("rejected", "100.00", "unknown")),
        ("100.00", "100.02", None, ("overpaid", "0.02", "overpaid")),
        ("100.00", "100.01", None, None),
    ],
)
def test_variance_table(
    claimed: str, paid: str, code: str | None, expected: tuple[str, str, str] | None
) -> None:
    case = _single(claimed, paid, code)
    if expected is None:
        assert case is None
    else:
        assert case is not None and (case.case_type, case.amount, case.reason_code) == (
            expected[0],
            D(expected[1]),
            expected[2],
        )


def test_zero_paid_without_code_is_short_paid_unexplained() -> None:
    """Owner decision 2026-11-12 (BUSINESS_LOGIC §10.3): fully unpaid, no rejection code -> short_paid for the full
    claimed amount (hand derivation: diff = 100.00 - 0.00 = 100.00), reason short_paid_unexplained."""
    case = _single("100.00", "0.00")
    assert case is not None
    assert (case.case_type, case.amount, case.reason_code) == (
        "short_paid",
        D("100.00"),
        "short_paid_unexplained",
    )


@pytest.mark.parametrize(
    ("reason", "facts", "expected"),
    [
        # GL-V1: unit price 88.00 equals sell price 88.00 -> our claim is supported
        ("rejected_price_mismatch", DisputeFacts(unit_price=D("88.00"), sell_price=D("88.00")), "supported"),
        ("rejected_price_mismatch", DisputeFacts(unit_price=D("88.00"), sell_price=D("86.00")), "weak"),
        (
            "rejected_price_mismatch",
            DisputeFacts(unit_price=D("88.00"), sell_price=D("88.04"), price_tolerance=D("0.05")),
            "supported",
        ),
        # CM-7781 line 3: consumption 100 within cap 100 (eligible 40)
        (
            "rejected_quantity_exceeds_authorization",
            DisputeFacts(consumption=D("100"), max_quantity=D("100"), eligible_quantity=D("40")),
            "supported",
        ),
        (
            "rejected_quantity_exceeds_authorization",
            DisputeFacts(consumption=D("101"), max_quantity=D("100")),
            "weak",
        ),
        (
            "rejected_agreement_expired",
            DisputeFacts(
                line_date=date(2026, 6, 30), valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)
            ),
            "supported",
        ),
        (
            "rejected_agreement_expired",
            DisputeFacts(line_date=date(2026, 7, 1), valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
            "weak",
        ),
        ("rejected_customer_ineligible", DisputeFacts(customer_in_scope=True), "supported"),
        ("rejected_customer_ineligible", DisputeFacts(customer_in_scope=False), "weak"),
        ("rejected_duplicate_claim", DisputeFacts(other_active_claims=0), "supported"),
        ("rejected_duplicate_claim", DisputeFacts(other_active_claims=1), "weak"),
        (
            "rejected_outside_claim_window",
            DisputeFacts(submitted_on=date(2026, 7, 5), claim_window_end=date(2026, 7, 5)),
            "supported",
        ),
        (
            "rejected_outside_claim_window",
            DisputeFacts(submitted_on=date(2026, 7, 6), claim_window_end=date(2026, 7, 5)),
            "weak",
        ),
        ("rejected_other", DisputeFacts(), "unknown"),
        ("rejected_price_mismatch", DisputeFacts(), "unknown"),  # inputs missing -> cannot check
    ],
)
def test_dispute_assessment(reason: str, facts: DisputeFacts, expected: str) -> None:
    result = assess_dispute(reason, facts)
    assert result.assessment == expected
    assert (len(result.checks) == 1) == (reason != "rejected_other")
    if result.checks:
        assert result.checks[0].inputs  # the assessment lists the inputs it used


def test_ground_truth_dispute_assessments_are_supported() -> None:
    texts = [e["dispute_assessment"] for e in EXP["expected"] if "dispute_assessment" in e]
    assert len(texts) == 2 and all(t.startswith("supported") for t in texts)


# ---- owner rule 2026-11-13 and review fixes (hand derivations) ----------------------------------------------------


def test_an_adjustment_in_another_batch_leaves_the_original_line_at_its_frozen_amount() -> None:
    """CL-4 frozen 250.00; the -50.00 ADJ rides in another batch, so the line carries no adjustment. The payment of
    200.00 matches by invoice line and all 200.00 goes to CL-4: claimed 250.00 - paid 200.00 = 50.00 short_paid, no
    code -> short_paid_unexplained. The adjustment item yields no finding (it has no classifier at all)."""
    cl4 = ClaimLine("c4", "M", "CL-4", "INV-10021", 1, D("250.00"))
    r = RemittanceLine("r4", "M", D("200.00"), invoice_no="INV-10021", invoice_line=1)
    res = reconcile([r], [cl4])
    (m,) = res.matches
    assert [(a.claim_line_id, a.adjustment_ref, a.amount) for a in m.allocations] == [
        ("c4", None, D("200.00"))
    ]
    case = classify_claim_line(cl4, res.matches, {"r4": r}, {})
    assert case is not None and (case.case_type, case.amount, case.reason_code) == (
        "short_paid",
        D("50.00"),
        "short_paid_unexplained",
    )


def test_an_allocation_to_an_adjustment_item_does_not_pay_its_original_line() -> None:
    cl4 = ClaimLine("c4", "M", "CL-4", "INV-10021", 1, D("250.00"))
    r = RemittanceLine("r", "M", D("100.00"))
    m = manual_adjustment_match(r, cl4, "adj1", D("-50.00"))
    assert m.claim_line_ids == frozenset() and paid_for("c4", [m]) == D(0)
    # the line is still untouched: no confirmed match touches it, so no case without a lag
    assert classify_claim_line(cl4, [m], {"r": r}, {}) is None


def test_the_same_batch_adjustment_is_still_split_off_the_payment_and_claimed_stays_frozen() -> None:
    """Ground-truth ordering: CL-4 250.00 + ADJ -50.00 in its own batch; payment 200.00 = 250.00 + (-50.00)."""
    cl4 = ClaimLine(
        "c4", "M", "CL-4", "INV-10021", 1, D("250.00"), adjustments=(Adjustment("a1", D("-50.00")),)
    )
    r = RemittanceLine("r4", "M", D("200.00"), invoice_no="INV-10021", invoice_line=1)
    res = reconcile([r], [cl4])
    (m,) = res.matches
    assert [(a.adjustment_ref, a.amount) for a in m.allocations] == [(None, D("250.00")), ("a1", D("-50.00"))]
    assert classify_claim_line(cl4, res.matches, {"r4": r}, {}) is None  # 250.00 - 250.00


def test_partial_allocation_reports_the_remainder_as_unmatched_payment() -> None:
    """V2: line 4,430.00 with only 2,200.00 confirmed -> 2,230.00 unmatched_payment, without any categorisation."""
    c = ClaimLine("c", "M", "CL-c", "I", 1, D("2200.00"))
    r = RemittanceLine("r", "M", D("4430.00"))
    m = manual_match(r, [(c, D("2200.00"))])
    case = classify_unmatched_payment(r, [m])
    assert case is not None and (case.case_type, case.amount) == ("unmatched_payment", D("2230.00"))
    assert classify_unmatched_payment(r, [manual_match(r, [(c, D("2200.00")), (c, D("2230.00"))])]) is None


def test_no_zero_finding_and_nothing_without_categorisation() -> None:
    zero = RemittanceLine("z", "M", D("0.00"))
    assert classify_unmatched_payment(zero, [], categorised=True) is None
    paid = RemittanceLine("p", "M", D("75.00"))
    assert classify_unmatched_payment(paid, []) is None
    c = ClaimLine("c", "M", "CL-c", "I", 1, D("660.00"))
    held = manual_match(
        zero, [(c, D("0.00"))]
    )  # a 0.00 manual match: the claim is rejected, nothing is unmatched
    assert classify_unmatched_payment(zero, [held]) is None
    rejected = classify_claim_line(
        c, [held], {"z": RemittanceLine("z", "M", D("0.00"), reason_code="E07")}, {"E07": "rejected_other"}
    )
    assert rejected is not None and (rejected.case_type, rejected.amount) == ("rejected", D("660.00"))


def test_paid_zero_with_a_code_mapped_to_a_non_rejection_reason_is_short_paid() -> None:
    c = ClaimLine("c", "M", "CL-c", "I", 1, D("100.00"))
    r = RemittanceLine("r", "M", D("0.00"), invoice_no="I", invoice_line=1, reason_code="X9")
    res = reconcile([r], [c])
    case = classify_claim_line(c, res.matches, {"r": r}, {"X9": "unmatched_payment"})
    assert case is not None and (case.case_type, case.amount, case.reason_code) == (
        "short_paid",
        D("100.00"),
        "unmatched_payment",
    )


def test_a_top_up_payment_finds_its_short_line_by_exact_identifier_only() -> None:
    """CL 250.00 was paid 200.00 by an earlier remittance (short 50.00). A later line of 50.00 naming its invoice line
    matches by T2; the same line with only SKU/qty/amount (T3) or an amount (T4/T5) is never offered the line."""
    c = ClaimLine("c", "M", "CL-c", "INV-1", 1, D("250.00"), sku="S", quantity=D("1"))
    t2 = RemittanceLine("t2", "M", D("50.00"), invoice_no="INV-1", invoice_line=1)
    got = reconcile([t2], [], topup_lines=[c])
    (m,) = got.matches
    assert (m.method, m.status, m.allocations[0].amount) == (
        Method.INVOICE_LINE,
        Status.CONFIRMED,
        D("50.00"),
    )
    t3 = RemittanceLine("t3", "M", D("250.00"), invoice_no="INV-1", sku="S", quantity=D("1"))
    assert reconcile([t3], [], topup_lines=[c]).matches == ()


def test_the_remainder_counts_claim_parts_only_and_stays_within_the_payment() -> None:
    """100.00 line with only ADJ -50.00 confirmed: claim parts 0, remainder 100.00 (not 150.00). A 200.00 line paid to
    CL 200.00 plus ADJ -50.00: remainder 0. The same-batch split 250.00 + (-50.00) on a 200.00 line: 200 - 250 < 0 -> none."""
    cl = ClaimLine("c", "M", "CL-c", "I", 1, D("250.00"))
    r100 = RemittanceLine("a", "M", D("100.00"))
    case = classify_unmatched_payment(r100, [manual_adjustment_match(r100, cl, "adj", D("-50.00"))])
    assert case is not None and case.amount == D("100.00")
    r200 = RemittanceLine("b", "M", D("200.00"))
    both = [manual_match(r200, [(cl, D("200.00"))]), manual_adjustment_match(r200, cl, "adj", D("-50.00"))]
    assert classify_unmatched_payment(r200, both) is None
    split = ClaimLine("s", "M", "CL-s", "I", 2, D("250.00"), adjustments=(Adjustment("a1", D("-50.00")),))
    (m,) = reconcile([RemittanceLine("z", "M", D("200.00"), invoice_no="I", invoice_line=2)], [split]).matches
    assert classify_unmatched_payment(RemittanceLine("z", "M", D("200.00")), [m]) is None
