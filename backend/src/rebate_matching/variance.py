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
    """``paid`` = Σ confirmed allocations to the claim line itself. Allocations to an adjustment item (even one that
    adjusts this line) belong to that item, not to the line (owner decision 2026-11-13)."""
    return sum(
        (
            a.amount
            for m in matches
            if m.status is Status.CONFIRMED
            for a in m.allocations
            if a.claim_line_id == claim_line_id and a.adjustment_ref is None
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
    claimed = claim.amount  # frozen; an adjustment is reconciled with the batch that carries it
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
    reason = map_reason(ext, reason_mappings)
    # paid = 0 with a code is a rejection when the code maps to a rejected_* reason or is unmapped (unknown); a code
    # mapped to any other reason is a short payment with that reason (falls through below)
    if paid == 0 and reason is not None and (reason == "unknown" or reason.startswith("rejected_")):
        return VarianceCase("rejected", abs(diff), reason)
    if paid > claimed + tolerance:
        return VarianceCase("overpaid", abs(diff), "overpaid")
    # paid = 0 without a rejection code: short_paid / short_paid_unexplained (owner decision 2026-11-12, §10.3)
    if 0 <= paid < claimed - tolerance:
        return VarianceCase("short_paid", diff, reason or "short_paid_unexplained")
    raise UnclassifiableVariance(f"claim line {claim.id}: claimed {claimed}, paid {paid}, no rejection code")


def classify_unmatched_payment(
    line: RemittanceLine, matches: Iterable[Match], *, categorised: bool = False
) -> VarianceCase | None:
    """The part of a remittance line that no confirmed match accounts for, as an ``unmatched_payment`` (§10.3).

    * No confirmed match: the full ``amount_paid``, but only once a person categorised the line (a line nobody decided
      on is refused at finalisation, never reported by default).
    * A confirmed match whose claim-line allocations cover less than ``amount_paid``: the remainder.
    * Never a 0.00 finding: a line paid 0.00 has nothing to report (its claim line is rejected via a 0.00 manual match).
    """
    confirmed = [m for m in matches if m.status is Status.CONFIRMED and m.remittance_line_id == line.id]
    if confirmed:
        # only the claim-line parts are cash applied to claims; an allocation to an adjustment item is signed and is not
        # cash on a claim line (D-N11 interim). Never below 0 (a same-batch split can exceed the payment) nor above it.
        claim_parts = sum(
            (a.amount for m in confirmed for a in m.allocations if a.adjustment_ref is None), Decimal(0)
        )
        amount = min(line.amount_paid, line.amount_paid - claim_parts)
    elif categorised:
        amount = line.amount_paid
    else:
        return None
    return VarianceCase("unmatched_payment", amount, "unmatched_payment") if amount > 0 else None
