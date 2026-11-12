"""§10.3 / §10.4 against expected_reconciliation.json (CM-7781) and hand-derived cases."""

from datetime import date
from decimal import Decimal

import pytest

from rebate_matching.dispute import DisputeFacts, assess_dispute
from rebate_matching.engine import reconcile
from rebate_matching.models import ClaimLine, RemittanceLine, Status
from rebate_matching.variance import (
    UnclassifiableVariance,
    classify_claim_line,
    classify_unmatched_payment,
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
    # GL-V2 lines are still undecided (suggestion, not confirmed): both would be reported unpaid by the caller
    assert got["CL-2026-000005"] is None or got["CL-2026-000005"].case_type == "unpaid"
    # unmatched payment (line 6): 75.00
    case6 = classify_unmatched_payment(rmap["6"], res.matches)
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


def test_zero_paid_without_code_is_a_spec_gap_and_fails_loudly() -> None:
    with pytest.raises(UnclassifiableVariance):
        _single("100.00", "0.00")


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
