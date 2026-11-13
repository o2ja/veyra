"""Input and output types of the matching package (BUSINESS_LOGIC.md §10). All frozen, all ``Decimal``."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Method(StrEnum):
    EXACT_REFERENCE = "exact_reference"  # T1
    INVOICE_LINE = "invoice_line"  # T2
    COMPOSITE = "composite"  # T3
    FUZZY = "fuzzy"  # T4
    SUBSET_SUM = "subset_sum"  # T5
    MANUAL = "manual"


class Status(StrEnum):
    CONFIRMED = "confirmed"
    SUGGESTED = "suggested"


def _check_money(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal, got {type(value).__name__}")


@dataclass(frozen=True)
class Adjustment:
    """An approved adjustment included in the claim batch (e.g. return reversal, negative amount)."""

    ref: str
    amount: Decimal

    def __post_init__(self) -> None:
        _check_money("adjustment amount", self.amount)


@dataclass(frozen=True)
class ClaimLine:
    """A submitted claim line. ``amount`` excludes adjustments; ``net_amount`` includes them (§10.3 ``claimed``)."""

    id: str
    manufacturer: str
    claim_reference: str
    invoice_no: str
    invoice_line: int | None
    amount: Decimal
    sku: str | None = None
    quantity: Decimal | None = None
    date: date | None = None
    adjustments: tuple[Adjustment, ...] = ()

    def __post_init__(self) -> None:
        _check_money("claim amount", self.amount)

    @property
    def net_amount(self) -> Decimal:
        return self.amount + sum((a.amount for a in self.adjustments), Decimal(0))


@dataclass(frozen=True)
class RemittanceLine:
    """One line of an accepted remittance document. ``manufacturer`` is the document's."""

    id: str
    manufacturer: str
    amount_paid: Decimal
    claim_reference: str | None = None
    invoice_no: str | None = None
    invoice_line: int | None = None
    sku: str | None = None
    quantity: Decimal | None = None
    date: date | None = None
    reason_code: str | None = None  # the manufacturer's external code (e.g. "E12")

    def __post_init__(self) -> None:
        _check_money("amount_paid", self.amount_paid)


@dataclass(frozen=True)
class Allocation:
    """``amount`` of a remittance line applied to a claim line (or to one of its adjustments)."""

    claim_line_id: str
    amount: Decimal
    adjustment_ref: str | None = None


@dataclass(frozen=True)
class Match:
    remittance_line_id: str
    method: Method
    status: Status
    allocations: tuple[Allocation, ...]
    evidence: dict[str, Any] = field(default_factory=dict)
    # True only for exact tiers (T1, T2, T3 with exactly equal amounts) and unambiguous; the caller
    # may persist those as confirmed without a human. Fuzzy/subset-sum/ambiguous never are.
    auto_confirm: bool = False

    @property
    def claim_line_ids(self) -> frozenset[str]:
        return frozenset(a.claim_line_id for a in self.allocations)

    @property
    def allocated(self) -> Decimal:
        return sum((a.amount for a in self.allocations), Decimal(0))


@dataclass(frozen=True)
class Note:
    """A search that was not concluded or was ambiguous (reported instead of guessing)."""

    remittance_line_id: str
    tier: Method
    code: str  # combination_limit_reached | assignment_limit_reached | ambiguous_subset_sum


@dataclass(frozen=True)
class MatchResult:
    matches: tuple[Match, ...]
    unmatched_remittance_ids: tuple[str, ...]
    unmatched_claim_ids: tuple[str, ...]
    notes: tuple[Note, ...] = ()
