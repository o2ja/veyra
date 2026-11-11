"""Request/response models for master data (API_SPECIFICATION.md §1.3: strict, unknown fields rejected,
money and factors as decimal strings)."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from rebate.core.money import Money
from rebate.core.validation import NO_CONTROL, STRICT, JsonUUID, Name
from rebate.modules.masterdata.pagination import Page


def _code(value: str) -> str:
    s = " ".join(value.split()).upper()
    if not s:
        raise ValueError("A code is required.")
    return s


def _uom(value: str) -> str:
    s = value.strip().upper()
    if not s.isalnum() or not s.isascii() or len(s) > 10:
        raise ValueError("Use 1-10 letters or digits.")
    return s


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


Code = Annotated[
    str, StringConstraints(min_length=1, max_length=50, pattern=NO_CONTROL), AfterValidator(_code)
]
ManufacturerCode = Annotated[
    str,
    BeforeValidator(_strip),
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$"),
    AfterValidator(_code),
]
Sku = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=NO_CONTROL), AfterValidator(_code)
]
Alias = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=NO_CONTROL), AfterValidator(_code)
]
Uom = Annotated[str, StringConstraints(min_length=1, max_length=10), AfterValidator(_uom)]
ShortText = Annotated[str, BeforeValidator(_strip), StringConstraints(max_length=100, pattern=NO_CONTROL)]
LongText = Annotated[str, BeforeValidator(_strip), StringConstraints(max_length=500, pattern=NO_CONTROL)]
Days = Annotated[int, Field(ge=1, le=3650)]


def _non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("Must not be negative.")
    return value


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("Must be greater than zero.")
    return value


def _scale(places: int, int_digits: int) -> AfterValidator:
    def check(value: Decimal) -> Decimal:
        exponent = value.normalize().as_tuple().exponent
        if isinstance(exponent, int) and exponent < -places:
            raise ValueError(f"At most {places} decimal places.")
        if abs(value) >= Decimal(10) ** int_digits:
            raise ValueError("Too large.")
        return value

    return AfterValidator(check)


Cost = Annotated[Money, _scale(6, 12), AfterValidator(_non_negative)]
Tolerance = Annotated[Money, _scale(4, 14), AfterValidator(_non_negative)]
Factor = Annotated[Money, _scale(8, 10), AfterValidator(_positive)]

CustomerType = Literal["contractor", "end_user", "branch", "other"]


class ManufacturerIn(BaseModel):
    model_config = STRICT
    name: Name
    code: ManufacturerCode
    external_id: ShortText | None = None
    active: bool = True


class ManufacturerPatch(BaseModel):
    model_config = STRICT
    name: Name | None = None
    external_id: ShortText | None = None
    active: bool | None = None


class ManufacturerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str
    external_id: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ManufacturerList(BaseModel):
    data: list[ManufacturerOut]
    page: Page


class ManufacturerSettingsIn(BaseModel):
    """PUT replaces every field (unset optional fields become null / their default)."""

    model_config = STRICT
    claim_window_days: Days | None = None
    claim_window_basis: Literal["invoice_date", "ship_date", "month_end", "period_end"] | None = None
    dispute_window_days: Days | None = None
    dispute_window_basis: Literal["remittance_date", "submission_date"] | None = None
    allow_negative_claims: bool = False
    conflict_policy: Literal["flag", "priority"] = "flag"
    variance_tolerance: Tolerance = Decimal("0.01")
    payment_lag_days_expected: Annotated[int, Field(ge=0, le=3650)] | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _pairs(self) -> ManufacturerSettingsIn:
        if (self.claim_window_days is None) != (self.claim_window_basis is None):
            raise ValueError("Set both claim_window_days and claim_window_basis, or neither.")
        if (self.dispute_window_days is None) != (self.dispute_window_basis is None):
            raise ValueError("Set both dispute_window_days and dispute_window_basis, or neither.")
        return self


class ManufacturerSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    manufacturer_id: UUID
    claim_window_days: int | None
    claim_window_basis: str | None
    dispute_window_days: int | None
    dispute_window_basis: str | None
    allow_negative_claims: bool
    conflict_policy: str
    variance_tolerance: Money
    payment_lag_days_expected: int | None
    notes: str | None
    revision: int
    updated_at: datetime


class ReasonCodeMappingIn(BaseModel):
    model_config = STRICT
    external_code: Annotated[
        str, BeforeValidator(_strip), StringConstraints(min_length=1, max_length=50, pattern=NO_CONTROL)
    ]
    external_text: LongText | None = None
    reason_code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,62}$")]


class ReasonCodeMappingsIn(BaseModel):
    model_config = STRICT
    mappings: Annotated[list[ReasonCodeMappingIn], Field(max_length=500)]


class ReasonCodeMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_code: str
    external_text: str | None
    reason_code: str


class ReasonCodeMappingList(BaseModel):
    data: list[ReasonCodeMappingOut]


class CustomerIn(BaseModel):
    model_config = STRICT
    name: Name
    code: Code | None = None
    external_id: ShortText | None = None
    customer_type: CustomerType = "other"
    parent_customer_id: JsonUUID | None = None
    active: bool = True


class CustomerPatch(BaseModel):
    model_config = STRICT
    name: Name | None = None
    code: Code | None = None
    external_id: ShortText | None = None
    customer_type: CustomerType | None = None
    parent_customer_id: JsonUUID | None = None
    active: bool | None = None


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    alias: str
    created_at: datetime


class CustomerAliasIn(BaseModel):
    model_config = STRICT
    alias: Alias
    source: ShortText | None = None


class CustomerAliasOut(AliasOut):
    customer_id: UUID
    source: str | None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str | None
    external_id: str | None
    customer_type: str
    parent_customer_id: UUID | None
    active: bool
    created_at: datetime
    updated_at: datetime


class CustomerDetail(CustomerOut):
    aliases: list[CustomerAliasOut]


class CustomerList(BaseModel):
    data: list[CustomerOut]
    page: Page


class BranchIn(BaseModel):
    model_config = STRICT
    name: Name
    code: Code


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str
    created_at: datetime


class BranchList(BaseModel):
    data: list[BranchOut]
    page: Page


class ProductIn(BaseModel):
    model_config = STRICT
    sku: Sku
    description: LongText | None = None
    manufacturer_id: JsonUUID
    manufacturer_part_number: ShortText | None = None
    base_uom: Uom
    category: ShortText | None = None
    list_cost: Cost | None = None
    active: bool = True


class ProductPatch(BaseModel):
    model_config = STRICT
    description: LongText | None = None
    manufacturer_id: JsonUUID | None = None
    manufacturer_part_number: ShortText | None = None
    base_uom: Uom | None = None
    category: ShortText | None = None
    list_cost: Cost | None = None
    active: bool | None = None


class ProductAliasIn(BaseModel):
    model_config = STRICT
    alias: Annotated[
        str, StringConstraints(min_length=1, max_length=100, pattern=NO_CONTROL), AfterValidator(_code)
    ]
    alias_type: Literal["erp_sku", "manufacturer_part", "upc", "other"]


class ProductAliasOut(AliasOut):
    product_id: UUID
    alias_type: str


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sku: str
    description: str | None
    manufacturer_id: UUID
    manufacturer_part_number: str | None
    base_uom: str
    category: str | None
    list_cost: Money | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ProductDetail(ProductOut):
    aliases: list[ProductAliasOut]


class ProductList(BaseModel):
    data: list[ProductOut]
    page: Page


class UomConversionIn(BaseModel):
    model_config = STRICT
    product_id: JsonUUID | None = None
    from_uom: Uom
    to_uom: Uom
    factor: Factor

    @model_validator(mode="after")
    def _distinct(self) -> UomConversionIn:
        if self.from_uom == self.to_uom:
            raise ValueError("from_uom and to_uom must differ.")
        return self


class UomConversionUpdate(BaseModel):
    model_config = STRICT
    factor: Factor


class UomConversionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID | None
    from_uom: str
    to_uom: str
    factor: Money
    created_at: datetime
    updated_at: datetime


class UomConversionList(BaseModel):
    data: list[UomConversionOut]
    page: Page


class UnmappedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: str
    external_value: str
    example_name: str | None
    status: str
    seen_count: int
    first_import_batch_id: UUID | None
    last_seen_at: datetime
    resolved_entity_id: UUID | None
    resolved_by: UUID | None
    resolved_at: datetime | None
    created_at: datetime


class UnmappedList(BaseModel):
    data: list[UnmappedOut]
    page: Page


class ResolveIn(BaseModel):
    model_config = STRICT
    action: Literal["map", "ignore"]
    target_id: JsonUUID | None = None
