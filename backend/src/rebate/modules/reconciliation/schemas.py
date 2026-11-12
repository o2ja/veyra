"""API models for remittances and reconciliation (API_SPECIFICATION.md §2.7). Money is a decimal string; ids are
UUIDs; unknown request fields are rejected."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from rebate.core.money import Money
from rebate.core.validation import NO_CONTROL, STRICT, JsonUUID
from rebate.modules.claims.schemas import JsonDate, RunJobRef
from rebate.modules.masterdata import Page

DocType = Literal["credit_memo", "remittance_advice", "deduction_notice"]
Channel = Literal["credit_memo", "check", "ach", "deduction"]
RemittanceStatus = Literal["uploaded", "pending_review", "accepted", "reconciled", "void"]
MatchStatus = Literal["suggested", "confirmed", "rejected"]
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=NO_CONTROL)
]
LongText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500, pattern=NO_CONTROL)
]


class RemittanceIn(BaseModel):
    model_config = STRICT

    file_id: JsonUUID
    manufacturer_id: JsonUUID
    doc_type: DocType
    channel: Channel
    mapping_template_id: JsonUUID | None = None
    document_number: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=NO_CONTROL)
        ]
        | None
    ) = None
    document_date: JsonDate | None = None
    payment_date: JsonDate | None = None
    total_amount: Money | None = None
    currency_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None

    @field_validator("total_amount")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("The total must not be negative.")
        return v


class RemittanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    manufacturer_id: UUID
    file_id: UUID
    doc_type: DocType
    channel: Channel
    document_number: str | None
    document_date: date | None
    payment_date: date | None
    total_amount: Money
    currency_code: str | None
    status: RemittanceStatus
    extraction_id: UUID | None
    mapping_template_id: UUID | None
    accepted_by: UUID | None
    accepted_at: datetime | None
    finalised_by: UUID | None
    finalised_at: datetime | None
    created_at: datetime


class RemittanceList(BaseModel):
    data: list[RemittanceOut]
    page: Page


class RemittanceAccepted(BaseModel):
    remittance: RemittanceOut
    job: RunJobRef


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_number: int
    source_row_number: int | None
    claim_reference: str | None
    invoice_number: str | None
    invoice_line_number: int | None
    sku_raw: str | None
    customer_ref: str | None
    quantity: Money | None
    amount_paid: Money
    amount_claimed_stated: Money | None
    external_reason_code: str | None
    external_reason_text: str | None
    reason_code: str | None


class LineList(BaseModel):
    data: list[LineOut]
    page: Page


class Issue(BaseModel):
    field: str
    code: str
    message: str


class StagedLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_number: int
    source_row_number: int | None
    claim_reference: str | None
    invoice_number: str | None
    invoice_line_number: int | None
    sku_raw: str | None
    customer_ref: str | None
    quantity: Money | None
    amount_paid: Money | None
    amount_claimed_stated: Money | None
    external_reason_code: str | None
    external_reason_text: str | None
    issues: list[Issue]
    corrected: bool


class ExtractionOut(BaseModel):
    remittance: RemittanceOut
    lines: list[StagedLineOut]
    problems: list[Issue]  # what blocks acceptance now (row issues and the arithmetic check)


class LineCorrection(BaseModel):
    """Only the fields sent are changed; ``null`` clears one."""

    model_config = STRICT

    line_number: Annotated[int, Field(ge=1, le=2_000_000_000)]
    claim_reference: Text | None = None
    invoice_number: Text | None = None
    invoice_line_number: Annotated[int, Field(ge=0, le=2_000_000_000)] | None = None
    sku: Text | None = None
    customer_ref: Text | None = None
    quantity: Money | None = None
    amount_paid: Money | None = None
    amount_claimed_stated: Money | None = None
    external_reason_code: Text | None = None
    external_reason_text: LongText | None = None


class AcceptIn(BaseModel):
    model_config = STRICT

    document_number: Text | None = None
    document_date: JsonDate | None = None
    payment_date: JsonDate | None = None
    total_amount: Money | None = None
    corrections: Annotated[list[LineCorrection], Field(max_length=5000)] = []


class AllocationOut(BaseModel):
    claim_line_id: UUID | None
    claim_adjustment_id: UUID | None
    claim_reference: str | None
    allocated_amount: Money


class MatchOut(BaseModel):
    id: UUID  # the match: one or more allocations of one remittance line
    remittance_line_id: UUID
    method: Literal["exact_reference", "invoice_line", "composite", "fuzzy", "subset_sum", "manual"]
    status: MatchStatus
    score: Money | None
    revision: int
    evidence: dict[str, Any]
    allocations: list[AllocationOut]
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    rejected_by: UUID | None
    rejected_at: datetime | None
    created_at: datetime


class MatchList(BaseModel):
    data: list[MatchOut]
    page: Page


class ManualMatchIn(BaseModel):
    model_config = STRICT

    remittance_line_id: JsonUUID
    claim_line_id: JsonUUID
    allocated_amount: Money

    @field_validator("allocated_amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("The allocated amount must be positive.")
        return v


class ReconciliationLineOut(BaseModel):
    line: LineOut
    state: Literal["matched", "suggested", "unmatched"]
    matches: list[MatchOut]


class ReconciliationOut(BaseModel):
    remittance: RemittanceOut
    data: list[ReconciliationLineOut]
    page: Page


class FindingOut(BaseModel):
    case_type: Literal["rejected", "short_paid", "overpaid", "unmatched_payment", "unpaid"]
    amount: Money
    reason_code: str
    claim_line_id: UUID | None
    remittance_line_id: UUID | None
    claimed: Money | None
    paid: Money
    external_reason_code: str | None


class FinaliseOut(BaseModel):
    remittance: RemittanceOut
    findings: list[FindingOut]
