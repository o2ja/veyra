"""Request/response models for imports, mapping templates and source lines (API_SPECIFICATION.md §2.4)."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rebate.core.money import Money
from rebate.core.validation import NO_CONTROL, STRICT, JsonUUID
from rebate.modules.masterdata import Page

ImportType = Literal["sales", "purchases", "customers", "products", "uom"]
ImportStatus = Literal[
    "uploaded", "scanning", "parsing", "awaiting_confirmation", "loading", "completed",
    "completed_with_errors", "failed", "rejected",
]  # fmt: skip
TemplateType = Literal["sales", "purchases", "customers", "products", "uom", "agreement_items", "remittance"]
Header = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=NO_CONTROL)]
# canonical field names are short snake_case; bounded so an unknown key is never echoed at length
FieldName = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]
SourceSystem = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")]
TemplateName = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=NO_CONTROL)]


class MappingOptions(BaseModel):
    model_config = STRICT
    date_order: Literal["MDY", "DMY"] = "MDY"
    decimal_separator: Literal[".", ","] = "."
    defaults: Annotated[
        dict[str, Annotated[str, StringConstraints(max_length=100)]], Field(max_length=50)
    ] = {}


class ImportIn(BaseModel):
    model_config = STRICT
    file_id: JsonUUID
    import_type: ImportType
    mapping_template_id: JsonUUID | None = None
    source_system: SourceSystem = "default"


class ImportCounts(BaseModel):
    rows_total: int
    valid: int
    rejected: int
    duplicates: int
    warnings: int
    inserted: int
    unchanged: int
    changed: int
    source_changed: int


class ImportOut(BaseModel):
    id: UUID
    import_type: str
    file_id: UUID
    mapping_template_id: UUID | None
    mapping_template_version: int | None
    source_system: str
    status: str
    accept_partial: bool
    counts: ImportCounts
    error_summary: str | None
    detected_encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    header_row: int | None
    headers: list[str] | None
    mapping: dict[str, str | None] | None
    options: dict[str, Any]
    mapping_revision: int
    needs_mapping: bool
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    completed_at: datetime | None


class ImportList(BaseModel):
    data: list[ImportOut]
    page: Page


class JobRef(BaseModel):
    id: UUID
    status: str
    progress: int
    result_url: str


class ImportAccepted(BaseModel):
    job: JobRef
    import_: ImportOut = Field(alias="import", serialization_alias="import")

    model_config = ConfigDict(populate_by_name=True)


class TemplateRef(BaseModel):
    name: TemplateName


class MappingIn(BaseModel):
    model_config = STRICT
    mapping: Annotated[dict[FieldName, Header | None], Field(max_length=50)]
    options: MappingOptions = MappingOptions()
    save_as_template: TemplateRef | None = None


class ConfirmIn(BaseModel):
    model_config = STRICT
    accept_partial: bool = False


class FieldOut(BaseModel):
    name: str
    kind: str
    required: bool


class IssueOut(BaseModel):
    column: str | None
    code: str
    message: str
    severity: str


class PreviewRow(BaseModel):
    row_number: int
    raw: dict[str, str]
    normalized: dict[str, Any] | None
    status: str
    issues: list[IssueOut]


class TemplateSummary(BaseModel):
    id: UUID
    name: str
    version: int | None


class PreviewOut(BaseModel):
    import_: ImportOut = Field(alias="import", serialization_alias="import")
    headers: list[str]
    suggested_mapping: dict[str, str | None]
    template: TemplateSummary | None
    fields: list[FieldOut]
    rows: list[PreviewRow]

    model_config = ConfigDict(populate_by_name=True)


class RowErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    row_number: int | None
    column_name: str | None
    code: str
    message: str
    severity: str
    raw: dict[str, str] | None


class RowErrorList(BaseModel):
    data: list[RowErrorOut]
    page: Page


class ChangeOut(BaseModel):
    line_id: UUID
    previous_line_id: UUID
    previous_import_batch_id: UUID
    document_number: str
    line_number: int
    source_row_number: int
    version: int
    source_changed: bool
    changed_fields: dict[str, list[str | None]]


class ChangeList(BaseModel):
    data: list[ChangeOut]
    page: Page


class TemplateIn(BaseModel):
    model_config = STRICT
    name: TemplateName
    import_type: TemplateType
    headers: Annotated[list[Header], Field(min_length=1, max_length=200)]
    mapping: Annotated[dict[FieldName, Header | None], Field(max_length=50)]
    options: MappingOptions = MappingOptions()


class TemplateUpdate(BaseModel):
    model_config = STRICT
    headers: Annotated[list[Header], Field(min_length=1, max_length=200)]
    mapping: Annotated[dict[FieldName, Header | None], Field(max_length=50)]
    options: MappingOptions = MappingOptions()


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    import_type: str
    header_signature: str
    mapping: dict[str, str | None]
    options: dict[str, Any]
    version: int
    created_by: UUID
    created_at: datetime
    updated_by: UUID | None
    updated_at: datetime


class TemplateList(BaseModel):
    data: list[TemplateOut]
    page: Page


class Lineage(BaseModel):
    import_batch_id: UUID
    file_id: UUID
    source_row_number: int
    source_system: str
    version: int
    is_current: bool
    superseded_by_id: UUID | None


class SalesLineOut(BaseModel):
    id: UUID
    document_id: UUID
    doc_type: str
    document_number: str
    line_number: int
    product_id: UUID | None
    sku_raw: str
    manufacturer_id: UUID | None
    customer_id: UUID
    description: str | None
    quantity: Money
    uom: str
    unit_price: Money
    unit_cost: Money | None
    replacement_cost: Money | None
    extended_amount: Money
    currency_code: str
    invoice_date: date
    ship_date: date | None
    spa_reference: str | None
    project_reference: str | None
    original_line_id: UUID | None
    source_changed: bool
    lineage: Lineage


class SalesLineDetail(SalesLineOut):
    previous_version_id: UUID | None


class SalesLineList(BaseModel):
    data: list[SalesLineOut]
    page: Page


class PurchaseLineOut(BaseModel):
    id: UUID
    document_id: UUID
    doc_type: str
    document_number: str
    line_number: int
    product_id: UUID | None
    sku_raw: str
    manufacturer_id: UUID
    quantity: Money
    uom: str
    unit_cost: Money
    extended_amount: Money
    currency_code: str
    document_date: date
    source_changed: bool
    lineage: Lineage


class PurchaseLineList(BaseModel):
    data: list[PurchaseLineOut]
    page: Page
