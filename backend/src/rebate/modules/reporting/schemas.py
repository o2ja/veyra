"""API models for the dashboard, drill-down, trace and audit pack (API_SPECIFICATION.md §2.9). Money is a decimal
string."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from rebate.core.money import Money
from rebate.core.validation import STRICT
from rebate.modules.claims import RunJobRef
from rebate.modules.masterdata import Page

Metric = Literal[
    "claimed",
    "collected",
    "short_paid",
    "recovered",
    "written_off",
    "recovered_after_write_off",
    "at_risk",
    "expiring",
]
TraceKind = Literal[
    "claim_line", "claim_adjustment", "remittance_line", "variance_case", "calculation_result"
]
ExportFormat = Literal["csv", "xlsx"]


class Figures(BaseModel):
    claimed: Money
    collected: Money
    short_paid: Money
    recovered: Money
    written_off: Money
    recovered_after_write_off: Money
    at_risk: Money
    expiring: Money


class MonthFigures(BaseModel):
    month: date
    figures: Figures


class DashboardOut(BaseModel):
    as_of: datetime | None  # when the summary was last rebuilt; null = never
    as_of_date: date | None  # the business date at-risk and expiring were computed for
    figures: Figures
    months: list[MonthFigures]


class DrillRow(BaseModel):
    id: UUID
    trace_kind: TraceKind | Literal["remittance_line"]
    trace_id: UUID
    manufacturer_id: UUID
    branch_id: UUID | None
    month: date
    amount: Money
    label: str


class DrilldownOut(BaseModel):
    metric: Metric
    as_of: datetime | None
    figure: Money  # the dashboard figure for the same filters
    total: Money  # Σ amount over all matching rows now
    consistent: bool  # total == figure (false only if data changed since the last refresh)
    data: list[DrillRow]
    page: Page


class TraceNode(BaseModel):
    kind: str
    id: UUID | None
    label: str
    fields: dict[str, str | None]


class TraceOut(BaseModel):
    kind: TraceKind
    id: UUID
    chain: list[TraceNode]


class RefreshAccepted(BaseModel):
    job_id: UUID | None  # null: a refresh was already queued this minute


class AuditPackIn(BaseModel):
    model_config = STRICT

    format: ExportFormat = "csv"


class AuditPackAccepted(BaseModel):
    job: RunJobRef
    file_id: UUID
