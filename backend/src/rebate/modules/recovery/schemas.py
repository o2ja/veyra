"""API models for cases, the queue and notifications (API_SPECIFICATION.md §2.8). Money is a decimal string;
unknown request fields are rejected."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, computed_field

from rebate.core.money import Money
from rebate.core.validation import NO_CONTROL_TEXT, STRICT, JsonUUID
from rebate.modules.masterdata import Page

CaseStatus = Literal["open", "to_reclaim", "submitted", "recovered", "written_off", "closed_no_action"]
CaseType = Literal["short_paid", "rejected", "unpaid", "overpaid", "unmatched_payment", "source_changed"]
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=NO_CONTROL_TEXT)
]


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    manufacturer_id: UUID
    claim_line_id: UUID | None
    remittance_line_id: UUID | None
    remittance_document_id: UUID | None
    case_type: CaseType
    reason_code: str
    external_reason_code: str | None
    claimed_amount: Money | None
    paid_amount: Money | None
    variance_amount: Money
    deadline_date: date | None
    deadline_basis: Literal["remittance_date", "submission_date"] | None
    owner_user_id: UUID | None
    status: CaseStatus
    recovered_amount: Money
    written_off_amount: Money
    dispute_assessment: dict[str, Any] | None
    revision: int
    write_off_proposed_by: UUID | None
    write_off_proposed_at: datetime | None
    write_off_reason: str | None
    written_off_by: UUID | None
    written_off_at: datetime | None
    created_at: datetime
    closed_at: datetime | None
    overdue_days: int | None = None  # live reclaimable case past its deadline, in days (business date)
    claim_batch_id: UUID | None = None
    claim_reference: str | None = None
    sales_document_line_id: UUID | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def deadline_unknown(self) -> bool:
        return self.deadline_date is None


class CaseList(BaseModel):
    data: list[CaseOut]
    page: Page


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    note: str | None
    payload: dict[str, Any] | None
    actor_user_id: UUID | None
    actor_name: str | None = None
    actor_email: str | None = None
    created_at: datetime


class EventList(BaseModel):
    data: list[EventOut]


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    file_id: UUID
    added_by: UUID
    added_at: datetime


class SummaryRow(BaseModel):
    key: str
    count: int
    variance_amount: Money
    recovered_amount: Money
    written_off_amount: Money  # what was written off, not the variance of written-off cases


class AtRisk(BaseModel):
    count: int
    amount: Money
    deadline_within_days: int


class Overdue(BaseModel):
    count: int
    amount: Money


class SummaryOut(BaseModel):
    by_status: list[SummaryRow]
    by_reason: list[SummaryRow]
    at_risk: AtRisk  # D-N6: deadline unknown, overdue or within 14 days
    overdue: Overdue  # deadline passed
    expiring: AtRisk  # deadline from today to 14 days ahead (not passed)


class CasePatch(BaseModel):
    model_config = STRICT

    owner_user_id: JsonUUID | None


class TransitionIn(BaseModel):
    model_config = STRICT

    to_status: CaseStatus
    note: Text | None = None


class NoteIn(BaseModel):
    model_config = STRICT

    note: Text


class AttachmentIn(BaseModel):
    model_config = STRICT

    file_id: JsonUUID


class WriteOffIn(BaseModel):
    model_config = STRICT

    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000, pattern=NO_CONTROL_TEXT)
    ]


class ReclaimOut(BaseModel):
    case: CaseOut
    adjustment_id: UUID


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    entity_type: str | None
    entity_id: UUID | None
    title: str
    body: str | None
    due_date: date | None
    read_at: datetime | None
    created_at: datetime


class NotificationList(BaseModel):
    data: list[NotificationOut]
    page: Page
