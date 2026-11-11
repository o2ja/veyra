"""API models for calculation runs, results, the accrual ledger and the preview (API_SPECIFICATION.md §2.8).
Money is a decimal string; ids are UUIDs; unknown request fields are rejected."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rebate.core.money import Money
from rebate.core.validation import STRICT, JsonUUID
from rebate.modules.masterdata import Page

ResultStatus = Literal["eligible", "exception", "netted", "adjustment", "not_applicable"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class RunIn(BaseModel):
    model_config = STRICT

    manufacturer_ids: list[JsonUUID] = Field(default_factory=list, max_length=500)
    date_from: date
    date_to: date
    as_of: datetime | None = None

    @model_validator(mode="after")
    def _window(self) -> RunIn:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from.")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("as_of must carry a time zone.")
        return self


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scope: dict[str, Any]
    as_of: datetime
    data_cutoff: datetime
    run_date: date
    engine_version: str
    config_hash: str
    agreement_version_ids: list[UUID]
    input_checksum: str | None
    status: RunStatus
    totals: dict[str, Any] | None
    error: dict[str, Any] | None
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobRef(BaseModel):
    id: UUID
    status: str
    progress: int
    result_url: str


class RunAccepted(BaseModel):
    job: JobRef
    run: RunOut


class RunList(BaseModel):
    data: list[RunOut]
    page: Page


class ResultOut(BaseModel):
    id: UUID
    run_id: UUID
    sales_document_line_id: UUID
    document_number: str
    line_number: int
    invoice_date: date
    manufacturer_id: UUID | None
    agreement_id: UUID | None
    agreement_version_id: UUID | None
    status: ResultStatus
    exception_code: str | None
    eligible_quantity: Money | None
    excess_quantity: Money | None
    claim_uom: str | None
    claim_unit_amount: Money | None
    claim_amount: Money
    flags: list[str]
    inputs_hash: str


class ResultDetail(ResultOut):
    trace: dict[str, Any]
    trace_ref: dict[str, str]


class ResultList(BaseModel):
    data: list[ResultOut]
    page: Page


class AccrualOut(BaseModel):
    id: UUID
    run_id: UUID
    agreement_id: UUID
    agreement_version_id: UUID
    external_number: str
    agreement_name: str
    period_start: date
    period_end: date
    as_of_date: date
    cumulative_qualifying: Money
    cumulative_payout_base: Money
    tier_number: int | None
    tier_rate: Money | None
    cumulative_earned: Money
    prior_cumulative_accrued: Money
    delta: Money
    claim_deadline: date | None
    flags: list[str]
    created_at: datetime


class AccrualList(BaseModel):
    data: list[AccrualOut]
    page: Page


class TierOut(BaseModel):
    tier_number: int
    lower_bound: Money
    rate: Money


class TrackerRow(BaseModel):
    agreement_id: UUID
    agreement_version_id: UUID
    external_number: str
    agreement_name: str
    manufacturer_id: UUID
    period_start: date
    period_end: date
    as_of_date: date
    cumulative_qualifying: Money
    cumulative_payout_base: Money
    tier_number: int | None
    tier_rate: Money | None
    next_tier_lower_bound: Money | None
    amount_to_next_tier: Money | None
    cumulative_earned: Money
    accrued_to_date: Money
    claim_deadline: date | None
    run_id: UUID
    tiers: list[TierOut]


class TrackerOut(BaseModel):
    data: list[TrackerRow]


class AdjustmentOut(BaseModel):
    id: UUID
    manufacturer_id: UUID
    adjustment_type: str
    claim_line_id: UUID | None
    calculation_result_id: UUID | None
    run_id: UUID | None
    amount: Money
    reason: str
    status: str


class ProposalsOut(BaseModel):
    run_id: UUID
    compared: int
    proposed: list[AdjustmentOut]


class PreviewIn(BaseModel):
    model_config = STRICT

    limit: int = Field(default=200, ge=1, le=200)


class PreviewLine(BaseModel):
    line_id: UUID
    document_number: str
    line_number: int
    invoice_date: date
    status: ResultStatus
    exception_code: str | None
    claim_amount: Money
    claim_unit_amount: Money | None
    eligible_quantity: Money | None
    flags: list[str]
    trace: dict[str, Any]


class PreviewAccrual(BaseModel):
    period_start: date
    period_end: date
    as_of_date: date
    cumulative_qualifying: Money
    cumulative_payout_base: Money
    tier_number: int | None
    tier_rate: Money | None
    cumulative_earned: Money
    claim_deadline: date | None
    flags: list[str]
    trace: dict[str, Any]
    note: str


class PreviewOut(BaseModel):
    kind: Literal["spa", "rebate"]
    persisted: Literal[False] = False
    run_date: date
    sample_size: int
    totals: dict[str, Any]
    lines: list[PreviewLine]
    accrual: PreviewAccrual | None
