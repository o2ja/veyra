"""API models for the missed-claims scan, export templates and claim batches (API_SPECIFICATION.md §2.8).
Money is a decimal string; ids are UUIDs; unknown request fields are rejected."""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from rebate.core.money import Money
from rebate.core.validation import NO_CONTROL, STRICT, JsonUUID
from rebate.modules.claims import exporting
from rebate.modules.claims.schemas import JsonDate, RunJobRef
from rebate.modules.masterdata import Page

BatchStatus = Literal["draft", "approved", "submitted", "void"]
ExportFormat = Literal["csv", "xlsx"]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=NO_CONTROL)]
Reference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=NO_CONTROL)
]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


# ---- missed claims -----------------------------------------------------------------------------------------


class MissedOut(BaseModel):
    id: UUID  # the calculation result: pass it as ``result_ids`` to build a batch
    run_id: UUID
    sales_document_line_id: UUID
    document_number: str
    line_number: int
    invoice_date: date
    manufacturer_id: UUID | None
    agreement_id: UUID | None
    agreement_version_id: UUID | None
    eligible_quantity: Money | None
    claim_uom: str | None
    claim_unit_amount: Money | None
    claim_amount: Money
    flags: list[str]


class MissedList(BaseModel):
    data: list[MissedOut]
    page: Page


class SummaryTotals(BaseModel):
    lines: int
    amount: Money


class SummaryGroup(BaseModel):
    manufacturer_id: UUID | None
    manufacturer_code: str | None
    manufacturer_name: str | None
    agreement_id: UUID | None
    agreement_number: str | None
    lines: int
    amount: Money


class SummaryException(BaseModel):
    code: str | None
    lines: int
    stored_amount: Money


class LostToExpiry(BaseModel):
    lines: int
    amount: Money


class MissedSummary(BaseModel):
    totals: SummaryTotals
    groups: list[SummaryGroup]
    exceptions: list[SummaryException]
    lost_to_expiry: LostToExpiry


class ExportAccepted(BaseModel):
    job: RunJobRef
    file_id: UUID


# ---- export templates --------------------------------------------------------------------------------------


class ColumnIn(BaseModel):
    model_config = STRICT

    header: ShortText
    source: str | None = None
    constant: Annotated[str, StringConstraints(max_length=200, pattern=NO_CONTROL)] | None = None
    format: str | None = None

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if (why := exporting.column_error(self.model_dump())) is not None:
            raise ValueError(why)
        return self


class OptionsIn(BaseModel):
    model_config = STRICT

    include_header: bool = True
    delimiter: Literal[",", ";", "\t", "|"] = ","
    sheet_name: Annotated[
        str, StringConstraints(min_length=1, max_length=31, pattern=r"^[^\\/*?:\[\]]+$")
    ] = "Claims"


class ExportTemplateBody(BaseModel):
    model_config = STRICT

    name: ShortText
    format: ExportFormat
    columns: Annotated[list[ColumnIn], Field(min_length=1, max_length=100)]
    options: OptionsIn = Field(default_factory=OptionsIn)
    active: bool = True


class ExportTemplateCreate(ExportTemplateBody):
    manufacturer_id: JsonUUID


class ExportTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    manufacturer_id: UUID
    name: str
    format: ExportFormat
    columns: list[dict[str, Any]]
    options: dict[str, Any]
    active: bool
    revision: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ExportTemplateList(BaseModel):
    data: list[ExportTemplateOut]
    page: Page


# ---- batches -----------------------------------------------------------------------------------------------


class BatchFilter(BaseModel):
    model_config = STRICT

    agreement_id: JsonUUID | None = None
    invoice_from: JsonDate | None = None
    invoice_to: JsonDate | None = None


class BatchIn(BaseModel):
    model_config = STRICT

    manufacturer_id: JsonUUID
    result_ids: Annotated[list[JsonUUID], Field(min_length=1, max_length=20000)] | None = None
    filter: BatchFilter | None = None

    @model_validator(mode="after")
    def _one_selection(self) -> Self:
        if (self.result_ids is None) == (self.filter is None):
            raise ValueError("Send exactly one of result_ids and filter.")
        return self


class BatchSubmitIn(BaseModel):
    model_config = STRICT

    submitted_at: JsonDate
    external_reference: Reference


class BatchVoidIn(BaseModel):
    model_config = STRICT

    reason: Reason


class BatchExportIn(BaseModel):
    model_config = STRICT

    template_id: JsonUUID | None = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    manufacturer_id: UUID
    batch_number: str
    status: BatchStatus
    period_from: date | None
    period_to: date | None
    line_count: int
    total_amount: Money
    export_template_id: UUID | None
    exported_file_id: UUID | None
    submitted_at: date | None
    submitted_by: UUID | None
    external_reference: str | None
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    void_reason: str | None
    voided_at: datetime | None


class BatchList(BaseModel):
    data: list[BatchOut]
    page: Page


class LineOut(BaseModel):
    id: UUID
    batch_id: UUID
    claim_reference: str
    source_kind: Literal["sales_line", "rebate_accrual"]
    sales_document_line_id: UUID | None
    rebate_accrual_entry_id: UUID | None
    calculation_result_id: UUID
    agreement_version_id: UUID
    document_number: str | None
    line_number: int | None
    invoice_date: date | None
    quantity: Money | None
    unit_amount: Money | None
    claim_amount: Money
    frozen_at: datetime | None
    released_at: datetime | None
    created_at: datetime


class LineList(BaseModel):
    data: list[LineOut]
    page: Page
