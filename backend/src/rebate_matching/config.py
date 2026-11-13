"""Matching configuration (BUSINESS_LOGIC.md §10.2). Recorded with each run by the caller (``asdict``)."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Weights:
    invoice: Decimal = Decimal("0.35")
    sku: Decimal = Decimal("0.25")
    quantity: Decimal = Decimal("0.15")
    amount: Decimal = Decimal("0.15")
    date: Decimal = Decimal("0.10")


@dataclass(frozen=True)
class MatchConfig:
    weights: Weights = Weights()
    # T3: |paid - claim| <= tolerance_wide, read as "5% of the claim OR $5" (either suffices).
    tolerance_wide_pct: Decimal = Decimal("0.05")
    tolerance_wide_abs: Decimal = Decimal("5.00")
    # T4
    fuzzy_threshold: Decimal = Decimal("0.80")
    date_full_days: int = 3  # date score 1 within +-3 days ...
    date_zero_days: int = 30  # ... linear down to 0 at +-30 days
    max_assignment_dim: int = 500  # rows/cols (after dropping lines with no candidate >= threshold)
    # T5
    subset_max_size: int = 12
    subset_max_combinations: int = 100_000


DEFAULT_CONFIG = MatchConfig()
