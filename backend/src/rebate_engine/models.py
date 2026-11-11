"""Frozen engine inputs and outputs (BUSINESS_LOGIC.md §3-§7, §13).

Plain values only (Decimal/str/date/int/bool/tuple) so the S5 orchestration can build them from DB rows.
Ids are opaque strings (UUIDs in the app, business codes in tests).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

type Json = str | int | bool | list[Json] | dict[str, Json] | None


class EngineRuleError(ValueError):
    """Inputs or rules the engine cannot evaluate without guessing (fail loudly, never default)."""


class Status(StrEnum):
    ELIGIBLE = "eligible"
    EXCEPTION = "exception"
    NETTED = "netted"
    ADJUSTMENT = "adjustment"
    NOT_APPLICABLE = "not_applicable"


class Code(StrEnum):
    """Exception codes, BUSINESS_LOGIC.md §8 (stable identifiers)."""

    UNKNOWN_PRODUCT = "unknown_product"
    NO_AGREEMENT = "no_agreement"
    INELIGIBLE_CUSTOMER = "ineligible_customer"
    INELIGIBLE_PRODUCT = "ineligible_product"
    AGREEMENT_NOT_STARTED = "agreement_not_started"
    EXPIRED_AGREEMENT = "expired_agreement"
    CONFLICTING_AGREEMENTS = "conflicting_agreements"
    MISSING_SPA_REFERENCE = "missing_spa_reference"
    SPA_REFERENCE_MISMATCH = "spa_reference_mismatch"
    MISSING_COST = "missing_cost"
    MISSING_REPLACEMENT_COST = "missing_replacement_cost"
    MISSING_UOM_CONVERSION = "missing_uom_conversion"
    PRICE_MISMATCH = "price_mismatch"
    CAP_EXHAUSTED = "cap_exhausted"
    NO_CLAIM_DUE = "no_claim_due"
    NEGATIVE_CLAIM_AMOUNT = "negative_claim_amount"
    OUTSIDE_CLAIM_WINDOW = "outside_claim_window"
    RETURN_ORIGINAL_MISSING = "return_original_missing"
    RETURN_EXCEEDS_ORIGINAL = "return_exceeds_original"


@dataclass(frozen=True, slots=True, kw_only=True)
class RoundingRule:
    mode: str  # half_up | half_even | down
    scale: int  # 0..6
    timing: str  # per_line | per_document


# ---- SPA -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SpaRules:
    schema_version: int
    date_basis: str  # invoice_date | ship_date
    basis: str  # price_difference | percent_of_sell | percent_of_cost | flat_per_unit | guaranteed_margin
    cost_source: str  # agreement_reference | line_cost | replacement_cost
    customer_scope_mode: str  # all | listed
    product_scope_mode: str  # listed
    spa_reference_required: bool
    price_check_mode: str  # flag | ignore
    price_tolerance: Decimal
    cap_scope: str  # per_item | per_agreement | none
    claim_uom: str | None
    rounding: RoundingRule
    adapter: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SpaItem:
    product_id: str
    reference_cost: Decimal | None = None
    deviated_cost: Decimal | None = None
    sell_price: Decimal | None = None
    rate: Decimal | None = None
    flat_amount: Decimal | None = None
    max_quantity: Decimal | None = None
    claim_uom: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerScope:
    customer_id: str
    scope: str  # include | exclude


@dataclass(frozen=True, slots=True, kw_only=True)
class SpaVersion:
    """An `agreement_versions` row of type spa that is active as of the run (the loader pins them)."""

    version_id: str
    agreement_id: str  # caps are tracked per agreement across its versions (split-supersede, §4.2/§9.2)
    external_number: str
    manufacturer_id: str
    valid_from: date
    valid_to: date
    priority: int
    rules: SpaRules
    items: tuple[SpaItem, ...]
    customers: tuple[CustomerScope, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ManufacturerSettings:
    """`manufacturer_settings` (DATABASE_SCHEMA.md); defaults are the DB defaults."""

    manufacturer_id: str
    conflict_policy: str = "flag"  # flag | priority
    claim_window_basis: str | None = (
        None  # invoice_date | ship_date | month_end (SPA lines); period_end applies to rebates only
    )
    claim_window_days: int | None = None
    allow_negative_claims: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class UomConversion:
    """1 `from_uom` = `factor` `to_uom`. `product_id` None = tenant default."""

    product_id: str | None
    from_uom: str
    to_uom: str
    factor: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class PriorClaim:
    """The active (frozen) claim line of an original sales line."""

    agreement_id: str
    agreement_version_id: str
    claim_uom: str
    quantity: Decimal  # claimed quantity in claim UoM
    unit_amount: Decimal
    claim_amount: Decimal
    rounding: RoundingRule


@dataclass(frozen=True, slots=True, kw_only=True)
class ReturnOriginal:
    """For a return line: its linked original line, as supplied by the adapter (§7)."""

    line_id: str
    quantity: Decimal  # original line quantity (positive)
    returned_quantity: Decimal  # sum |qty| of all linked current returns dated <= data_cutoff
    prior_claim: PriorClaim | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SalesLine:
    """A sales document line. Return (credit-note) lines have negative `quantity` (normalised at import)."""

    line_id: str
    document_number: str
    line_number: int
    invoice_date: date
    ship_date: date | None
    customer_id: str
    product_id: str | None  # None = unknown product
    manufacturer_id: str | None
    base_uom: str | None
    quantity: Decimal
    uom: str
    unit_price: Decimal
    customer_ancestor_ids: tuple[str, ...] = ()  # parent, grandparent, ... (§8 inheritance)
    unit_cost: Decimal | None = None
    replacement_cost: Decimal | None = None
    spa_reference: str | None = None
    # originals only (§7): sum |qty| of linked current returns dated <= data_cutoff, and the active claim line
    returned_quantity: Decimal = Decimal(0)
    prior_claim: PriorClaim | None = None
    # return lines only
    return_original: ReturnOriginal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SpaRunInput:
    run_date: date
    lines: tuple[SalesLine, ...]
    versions: tuple[SpaVersion, ...]
    manufacturers: tuple[ManufacturerSettings, ...] = ()
    conversions: tuple[UomConversion, ...] = ()
    # (agreement_id, product_id) for per_item caps -> quantity consumed by claim lines in frozen/active
    # batches, net of return adjustments, EXCLUDING source lines that are part of this run's `lines`.
    prior_consumption: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class LineResult:
    """One `calculation_results` row."""

    line_id: str
    status: Status
    exception_code: Code | None
    agreement_version_id: str | None
    claim_amount: Decimal
    claim_unit_amount: Decimal | None = None
    eligible_quantity: Decimal | None = None
    excess_quantity: Decimal | None = None
    claim_uom: str | None = None
    flags: tuple[str, ...] = ()
    trace: dict[str, Json] = field(default_factory=dict, compare=False)
    inputs_hash: str = ""


# ---- Rebates ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RebateRules:
    schema_version: int
    volume_basis: str  # purchases | sales
    period_start: date
    period_end: date
    tier_mode: str  # retroactive | incremental
    tier_metric: str  # amount | quantity
    product_scope_mode: str  # all | listed
    spa_lines: str  # include | qualify_only | exclude
    returns: str  # reduce_volume
    rounding: RoundingRule


@dataclass(frozen=True, slots=True, kw_only=True)
class Tier:
    tier_number: int
    lower_bound: Decimal
    rate: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RebateVersion:
    version_id: str
    external_number: str
    manufacturer_id: str
    rules: RebateRules
    tiers: tuple[Tier, ...]
    product_ids: tuple[str, ...] = ()  # items, for product_scope = listed


@dataclass(frozen=True, slots=True, kw_only=True)
class VolumeLine:
    """A purchase receipt / vendor credit or sales / credit-note line; credits carry negative amounts."""

    line_id: str
    line_date: date
    manufacturer_id: str
    product_id: str
    amount: Decimal  # signed net extended amount
    quantity: Decimal  # signed
    spa_reference: str | None = None
    spa_eligible: bool = False  # eligible under an SPA agreement in the same run (§5.4)


@dataclass(frozen=True, slots=True, kw_only=True)
class AccrualResult:
    """One `rebate_accrual_entries` row (append-only ledger)."""

    agreement_version_id: str
    period_start: date
    period_end: date
    as_of: date
    qualifying_volume: Decimal
    payout_volume: Decimal
    tier_number: int | None
    rate: Decimal | None
    earned_cumulative: Decimal
    prior_cumulative_accrued: Decimal
    delta: Decimal
    flags: tuple[str, ...] = ()
    trace: dict[str, Json] = field(default_factory=dict, compare=False)
    inputs_hash: str = ""
