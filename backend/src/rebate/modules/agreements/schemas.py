"""Agreement payload schema (BUSINESS_LOGIC.md §3.3, ``payload_schema_version`` 1) and API models.

The payload model is the *structural* half of the AgreementValidator: strict types, unknown fields rejected,
money/rates/quantities as decimal strings that fit their NUMERIC columns. It accepts exactly the shape the
engine parses (``rebate_engine.rules``); documented Release 2 values (``cap_scope = per_agreement``,
``tier_metric = quantity``) pass here and are refused by the engine parser with
``not_supported_until_release_2``. Drafts are stored even when the payload does not validate (work in
progress, S6 extraction drafts); the validator reports why.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints
from pydantic_core import PydanticCustomError

from rebate.core.money import Money
from rebate.core.validation import NO_CONTROL, STRICT, JsonUUID
from rebate.modules.masterdata import Page

_DECIMAL = re.compile(r"^-?\d{1,20}(\.\d{1,20})?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ITEMS = 20_000
MAX_CUSTOMERS = 10_000
MAX_TIERS = 100


def _decimal(
    scale: int, int_digits: int, *, positive: bool = False, maximum: Decimal | None = None
) -> AfterValidator:
    """A decimal **string** that fits NUMERIC(int_digits + scale, scale); never a JSON number."""

    def check(value: str) -> str:
        if not _DECIMAL.fullmatch(value):
            raise PydanticCustomError("invalid_decimal", 'Use a decimal string such as "12.50".')
        d = Decimal(value)
        if d < 0:
            raise PydanticCustomError("out_of_range", "Must not be negative.")
        if positive and d == 0:
            raise PydanticCustomError("out_of_range", "Must be greater than zero.")
        if maximum is not None and d > maximum:
            raise PydanticCustomError("out_of_range", "Must be at most {maximum}.", {"maximum": str(maximum)})
        exponent = d.normalize().as_tuple().exponent
        if isinstance(exponent, int) and exponent < -scale:
            raise PydanticCustomError(
                "too_many_decimals", "At most {scale} decimal places.", {"scale": scale}
            )
        if d >= Decimal(10) ** int_digits:
            raise PydanticCustomError("out_of_range", "The number is too large.")
        return value

    return AfterValidator(check)


def _iso_date(value: str) -> str:
    try:
        if not _ISO_DATE.fullmatch(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise PydanticCustomError("invalid_date", "Use a date such as 2026-01-31.") from None
    return value


Cost = Annotated[str, _decimal(6, 12)]  # NUMERIC(18,6)
Quantity = Annotated[str, _decimal(6, 12, positive=True)]
Rate = Annotated[str, _decimal(6, 3, maximum=Decimal(1))]  # NUMERIC(9,6), a fraction in [0, 1]
LowerBound = Annotated[str, _decimal(2, 16)]  # NUMERIC(18,2)
IsoDate = Annotated[str, AfterValidator(_iso_date)]
Code50 = Annotated[str, StringConstraints(min_length=1, max_length=50, pattern=NO_CONTROL)]
Code100 = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=NO_CONTROL)]
Uom = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,10}$")]
Text200 = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=NO_CONTROL)]


class _Strict(BaseModel):
    model_config = STRICT


class Header(_Strict):
    agreement_type: Literal["spa", "rebate"]
    manufacturer_code: Code50
    external_number: Code100
    name: Text200
    valid_from: IsoDate
    valid_to: IsoDate
    priority: Annotated[int, Field(ge=-1000, le=1000)] = 0


class Rounding(_Strict):
    mode: Literal["half_up", "half_even", "down"]
    scale: Annotated[int, Field(ge=0, le=6)]
    timing: Literal["per_line", "per_document"]


class Calculation(_Strict):
    basis: Literal[
        "price_difference", "percent_of_sell", "percent_of_cost", "flat_per_unit", "guaranteed_margin"
    ]
    cost_source: Literal["agreement_reference", "line_cost", "replacement_cost"]


class CustomerScope(_Strict):
    mode: Literal["all", "listed"]


class SpaProductScope(_Strict):
    mode: Literal["listed"]


class SpaReference(_Strict):
    required: bool


class PriceCheck(_Strict):
    mode: Literal["flag", "ignore"]
    tolerance: Cost


class QuantityRule(_Strict):
    cap_scope: Literal["per_item", "none", "per_agreement"]


class SpaRules(_Strict):
    schema_version: Literal[1]
    type: Literal["spa"]
    date_basis: Literal["invoice_date", "ship_date"]
    calculation: Calculation
    customer_scope: CustomerScope
    product_scope: SpaProductScope
    spa_reference: SpaReference
    price_check: PriceCheck
    quantity: QuantityRule
    claim_uom: Uom | None = None
    rounding: Rounding
    adapter: Code50 | None = None


class MeasurementPeriod(_Strict):
    kind: Code50
    start: IsoDate
    end: IsoDate


class RebateProductScope(_Strict):
    mode: Literal["all", "listed"]


class RebateRules(_Strict):
    schema_version: Literal[1]
    type: Literal["rebate"]
    volume_basis: Literal["purchases", "sales"]
    measurement_period: MeasurementPeriod
    tier_mode: Literal["retroactive", "incremental"]
    tier_metric: Literal["amount", "quantity"]
    product_scope: RebateProductScope
    spa_lines: Literal["include", "qualify_only", "exclude"]
    returns: Literal["reduce_volume"]
    rounding: Rounding


class CustomerRef(_Strict):
    customer_code: Code50
    scope: Literal["include", "exclude"]


class Item(_Strict):
    sku: Code100
    reference_cost: Cost | None = None
    deviated_cost: Cost | None = None
    sell_price: Cost | None = None
    rate: Rate | None = None
    flat_amount: Cost | None = None
    max_quantity: Quantity | None = None
    claim_uom: Uom | None = None


class TierIn(_Strict):
    tier_number: Annotated[int, Field(ge=1, le=MAX_TIERS)]
    lower_bound: LowerBound
    rate: Rate


class Payload(_Strict):
    payload_schema_version: Literal[1]
    header: Header
    rules: Annotated[SpaRules | RebateRules, Field(discriminator="type")]
    customers: Annotated[list[CustomerRef], Field(max_length=MAX_CUSTOMERS)] = []
    items: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = []
    tiers: Annotated[list[TierIn], Field(max_length=MAX_TIERS)] = []


# ---- API requests -------------------------------------------------------------------------------------

LongText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Source = Literal["manual", "spreadsheet_template", "ai_extraction"]


class DraftCreate(_Strict):
    source: Source
    agreement_id: JsonUUID | None = None
    amends_version_id: JsonUUID | None = None
    payload: dict[str, Any] | None = None
    source_file_id: JsonUUID | None = None


class DraftUpdate(_Strict):
    payload: dict[str, Any]
    change_summary: Annotated[str, StringConstraints(max_length=500, pattern=NO_CONTROL)] | None = None


class SubmitIn(_Strict):
    acknowledged_warnings: Annotated[
        list[Annotated[str, StringConstraints(max_length=300)]], Field(max_length=5000)
    ] = []


class ReturnIn(_Strict):
    comment: LongText


class RejectIn(_Strict):
    reason: LongText


ITEM_FIELDS = ("sku", "reference_cost", "deviated_cost", "sell_price", "rate", "flat_amount", "max_quantity",
               "claim_uom")  # fmt: skip
ItemField = Literal["sku", "reference_cost", "deviated_cost", "sell_price", "rate", "flat_amount", "max_quantity",
                    "claim_uom"]  # fmt: skip


class ItemsImportIn(_Strict):
    file_id: JsonUUID
    mapping: dict[ItemField, Annotated[str, StringConstraints(min_length=1, max_length=200)]] | None = None
    rate_format: Literal["fraction", "percent"]
    header_row: Annotated[int, Field(ge=1, le=1000)] | None = None
    sheet: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    dry_run: bool = False


class CancelIn(_Strict):
    effective_date: Annotated[date, Field(strict=False)]
    reason: LongText


# ---- API responses ------------------------------------------------------------------------------------


class Issue(BaseModel):
    field: str
    code: str
    message: str
    id: str | None = None  # warnings only: the value to acknowledge at submit


class References(BaseModel):
    unresolved_customers: list[str]
    unresolved_skus: list[str]


class Validation(BaseModel):
    valid: bool
    revision: int
    validated_at: datetime
    error_count: int
    errors: list[Issue]
    warnings: list[Issue]
    references: References


class DraftSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agreement_id: UUID | None
    amends_version_id: UUID | None
    source: str
    status: str
    revision: int
    payload_schema_version: int
    source_file_id: UUID | None
    extraction_id: UUID | None
    created_by: UUID
    submitted_by: UUID | None
    submitted_at: datetime | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    review_comment: str | None
    resulting_version_id: UUID | None
    created_at: datetime
    updated_at: datetime


class DraftOut(DraftSummary):
    payload: dict[str, Any]
    validation: Validation | None


class DraftList(BaseModel):
    data: list[DraftSummary]
    page: Page


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agreement_id: UUID
    version_number: int
    status: str
    valid_from: date
    valid_to: date
    priority: int
    source: str
    draft_id: UUID | None
    derived_from_version_id: UUID | None
    supersedes_version_id: UUID | None
    superseded_by_version_id: UUID | None
    content_hash: str
    created_by: UUID
    approved_by: UUID
    activated_at: datetime
    superseded_at: datetime | None


class CustomerOut(BaseModel):
    customer_id: UUID
    customer_code: str | None
    scope: str


class TierOut(BaseModel):
    tier_number: int
    lower_bound: Money
    rate: Money | None


class VersionOut(VersionSummary):
    agreement_type: str
    manufacturer_id: UUID
    external_number: str
    name: str
    rules: dict[str, Any]
    rules_schema_version: int
    customers: list[CustomerOut]
    tiers: list[TierOut]
    item_count: int


class VersionList(BaseModel):
    data: list[VersionSummary]
    page: Page


class ItemOut(BaseModel):
    id: UUID
    line_number: int
    product_id: UUID
    sku: str
    customer_id: UUID | None
    reference_cost: Money | None
    deviated_cost: Money | None
    sell_price: Money | None
    flat_amount: Money | None
    rate: Money | None
    claim_uom: str | None
    max_quantity: Money | None


class ItemList(BaseModel):
    data: list[ItemOut]
    page: Page


class AgreementOut(BaseModel):
    id: UUID
    manufacturer_id: UUID
    agreement_type: str
    external_number: str
    name: str
    cancelled_at: datetime | None
    created_by: UUID
    created_at: datetime
    current_version: VersionSummary | None  # the active version covering the business date today


class AgreementList(BaseModel):
    data: list[AgreementOut]
    page: Page


class Change(BaseModel):
    path: str
    before: Any
    after: Any


class DiffOut(BaseModel):
    from_version_id: UUID
    to_version_id: UUID
    changes: list[Change]


class ItemsImportOut(BaseModel):
    dry_run: bool
    rows: int
    items: list[dict[str, Any]]  # dry run: the first 100 parsed items
    draft: DraftOut | None
