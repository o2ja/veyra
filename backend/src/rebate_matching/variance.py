"""Variance classification (BUSINESS_LOGIC.md §10.3). Pure; the caller supplies dates and mappings.

Not covered here: ``source_changed`` (detected by comparing source rows, not from a match result).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from rebate_matching.models import ClaimLine, Match, RemittanceLine, Status

CaseType = Literal["rejected", "short_paid", "overpaid", "unmatched_payment", "unpaid"]
DEFAULT_TOLERANCE = Decimal("0.01")


class UnclassifiableVariance(Exception):
    """A difference that §10.3 has no row for (paid = 0, no rejection code, line was on a remittance)."""


@dataclass(frozen=True)
class VarianceCase:
    case_type: CaseType
    amount: Decimal  # always positive
    reason_code: str


def paid_for(claim_line_id: str, matches: Iterable[Match]) -> Decimal:
    """``paid`` = Σ confirmed allocations (claim and adjustment parts) on the claim line."""
    return sum(
        (
            a.amount
            for m in matches
            if m.status is Status.CONFIRMED
            for a in m.allocations
            if a.claim_line_id == claim_line_id
        ),
        Decimal(0),
    )


def map_reason(external_code: str | None, mappings: Mapping[str, str]) -> str | None:
    """External code -> controlled reason code; an unmapped code is ``unknown`` (never guessed)."""
    if not external_code:
        return None
    return mappings.get(external_code, "unknown")


def classify_claim_line(
    claim: ClaimLine,
    matches: Iterable[Match],
    remittance_lines: Mapping[str, RemittanceLine],
    reason_mappings: Mapping[str, str],
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
    as_of: date | None = None,
    submitted_at: date | None = None,
    payment_lag_days: int | None = None,
) -> VarianceCase | None:
    """Classify one claim line; ``None`` means no case.

    ``unpaid`` needs ``as_of``, ``submitted_at`` and ``payment_lag_days`` (configured); it applies only to a
    line that no confirmed match touches, once ``as_of`` is after ``submitted_at + payment_lag_days``.
    """
    touching = [m for m in matches if m.status is Status.CONFIRMED and claim.id in m.claim_line_ids]
    paid = paid_for(claim.id, touching)
    claimed = claim.net_amount
    diff = claimed - paid
    if not touching:
        if (
            as_of is not None
            and submitted_at is not None
            and payment_lag_days is not None
            and abs(diff) > tolerance
            and as_of > submitted_at + timedelta(days=payment_lag_days)
        ):
            return VarianceCase("unpaid", abs(diff), "unpaid_no_remittance")
        return None
    if abs(diff) <= tolerance:
        return None
    codes = [remittance_lines[m.remittance_line_id].reason_code for m in touching]
    ext = next((c for c in codes if c), None)
    if paid == 0 and ext:
        return VarianceCase("rejected", abs(diff), map_reason(ext, reason_mappings) or "unknown")
    if paid > claimed + tolerance:
        return VarianceCase("overpaid", abs(diff), "overpaid")
    # paid = 0 without a rejection code: short_paid / short_paid_unexplained (owner decision 2026-11-12, §10.3)
    if 0 <= paid < claimed - tolerance:
        return VarianceCase("short_paid", diff, map_reason(ext, reason_mappings) or "short_paid_unexplained")
    raise UnclassifiableVariance(f"claim line {claim.id}: claimed {claimed}, paid {paid}, no rejection code")


def classify_unmatched_payment(line: RemittanceLine, matches: Iterable[Match]) -> VarianceCase | None:
    """A remittance line with no accepted (confirmed) match is an ``unmatched_payment`` for its full amount."""
    if any(m.status is Status.CONFIRMED and m.remittance_line_id == line.id for m in matches):
        return None
    return VarianceCase("unmatched_payment", line.amount_paid, "unmatched_payment")
