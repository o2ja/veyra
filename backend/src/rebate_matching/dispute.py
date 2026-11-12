"""Dispute assessment (BUSINESS_LOGIC.md §10.4). Pure, no LLM.

``supported`` = the re-check shows OUR claim holds against the manufacturer's stated reason; ``weak`` = the check
shows the manufacturer's reason holds; ``unknown`` = reason not checkable (no rule, or inputs missing).
The caller resolves the line date per the agreement's ``date_basis`` before calling.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

Assessment = Literal["supported", "weak", "unknown"]


@dataclass(frozen=True)
class DisputeFacts:
    line_date: date | None = None  # per date_basis
    valid_from: date | None = None  # pinned agreement version valid_range (inclusive)
    valid_to: date | None = None
    customer_in_scope: bool | None = None
    unit_price: Decimal | None = None
    sell_price: Decimal | None = None
    price_tolerance: Decimal = Decimal(0)
    consumption: Decimal | None = None
    max_quantity: Decimal | None = None
    eligible_quantity: Decimal | None = None  # shown in the inputs only
    other_active_claims: int | None = None  # other active claim lines / payments for the same source line
    submitted_on: date | None = None
    claim_window_end: date | None = None


@dataclass(frozen=True)
class Check:
    name: str
    inputs: dict[str, str]
    passed: bool | None  # None = could not be run (missing input)


@dataclass(frozen=True)
class DisputeAssessment:
    assessment: Assessment
    checks: tuple[Check, ...]


def _s(**kw: object) -> dict[str, str]:
    return {k: "" if v is None else str(v) for k, v in kw.items()}


def _expired(f: DisputeFacts) -> Check:
    ins = _s(line_date=f.line_date, valid_from=f.valid_from, valid_to=f.valid_to)
    if f.line_date is None or f.valid_from is None or f.valid_to is None:
        return Check("line_date_in_valid_range", ins, None)
    return Check("line_date_in_valid_range", ins, f.valid_from <= f.line_date <= f.valid_to)


def _customer(f: DisputeFacts) -> Check:
    return Check("customer_in_scope", _s(customer_in_scope=f.customer_in_scope), f.customer_in_scope)


def _price(f: DisputeFacts) -> Check:
    ins = _s(unit_price=f.unit_price, sell_price=f.sell_price, tolerance=f.price_tolerance)
    if f.unit_price is None or f.sell_price is None:
        return Check("unit_price_matches_sell_price", ins, None)
    return Check("unit_price_matches_sell_price", ins, abs(f.unit_price - f.sell_price) <= f.price_tolerance)


def _quantity(f: DisputeFacts) -> Check:
    ins = _s(consumption=f.consumption, max_quantity=f.max_quantity, eligible_quantity=f.eligible_quantity)
    if f.consumption is None or f.max_quantity is None:
        return Check("consumption_within_max_quantity", ins, None)
    return Check("consumption_within_max_quantity", ins, f.consumption <= f.max_quantity)


def _duplicate(f: DisputeFacts) -> Check:
    ins = _s(other_active_claims=f.other_active_claims)
    if f.other_active_claims is None:
        return Check("no_other_active_claim_or_payment", ins, None)
    return Check("no_other_active_claim_or_payment", ins, f.other_active_claims == 0)


def _window(f: DisputeFacts) -> Check:
    ins = _s(submitted_on=f.submitted_on, claim_window_end=f.claim_window_end)
    if f.submitted_on is None or f.claim_window_end is None:
        return Check("submitted_within_claim_window", ins, None)
    return Check("submitted_within_claim_window", ins, f.submitted_on <= f.claim_window_end)


_CHECKS: dict[str, Callable[[DisputeFacts], Check]] = {
    "rejected_agreement_expired": _expired,
    "rejected_customer_ineligible": _customer,
    "rejected_price_mismatch": _price,
    "rejected_quantity_exceeds_authorization": _quantity,
    "rejected_duplicate_claim": _duplicate,
    "rejected_outside_claim_window": _window,
}


def assess_dispute(reason_code: str, facts: DisputeFacts) -> DisputeAssessment:
    """Run the single check for ``reason_code``; other reasons are ``unknown`` with no checks."""
    check = _CHECKS.get(reason_code)
    if check is None:
        return DisputeAssessment("unknown", ())
    result = check(facts)
    if result.passed is None:
        return DisputeAssessment("unknown", (result,))
    return DisputeAssessment("supported" if result.passed else "weak", (result,))
