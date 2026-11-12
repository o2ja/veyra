"""Extraction API: start/read agreement extractions, AI usage, AI column-mapping suggestion.

Permissions (SECURITY.md §3): reads ``data.read``; start an extraction ``agreement.draft``; mapping suggestion
``ai.use``. The opt-in (``ai_processing_enabled``) is checked in the service: a disabled workspace gets 409
``ai_not_enabled`` and no model is ever called. The workspace's own opt-in setting is
``PATCH /settings`` (tenancy).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from rebate.core.access import require, tenant_of
from rebate.core.errors import ValidationFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.core.validation import STRICT, JsonUUID
from rebate.modules.extraction import mapping, service
from rebate.modules.extraction.models import DocumentExtraction
from rebate.modules.masterdata import Who

router = APIRouter(tags=["extraction"])

READ = "data.read"
DRAFT = "agreement.draft"


# ---- result view (``ExtractionDetail.result``): published in OpenAPI for the review UI --------------------
# forbid: the stored view must match these models exactly (a test validates every golden result).


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractionEvidence(_View):
    page: int | None
    sheet: str | None
    cell: str | None
    quote: str
    start: int
    end: int
    boxes: list[list[float]]


class ExtractionPassView(_View):
    status: str
    value: Any
    reasons: list[str]
    evidence: ExtractionEvidence | None


class ExtractionFieldView(_View):
    key: str
    critical: bool
    value: Any
    confidence: str
    reasons: list[str]
    evidence: ExtractionEvidence | None
    pass_a: ExtractionPassView | None
    pass_b: ExtractionPassView | None


class ExtractionIndicator(_View):
    kind: str
    excerpt: str
    page: int | None
    sheet: str | None
    cell: str | None
    start: int | None
    end: int | None


class ExtractionResult(_View):
    schema_version: int
    agreement_type: str | None
    payload: dict[str, Any]
    fields: list[ExtractionFieldView]
    blocking: list[str]
    needs_acknowledgement: list[str]
    indicators: list[ExtractionIndicator]
    draft_id: str
    notes: dict[str, list[str]]
    unresolved: dict[str, Any]


class ExtractionStart(BaseModel):
    model_config = STRICT
    file_id: JsonUUID
    kind: str = "agreement"
    rerun: bool = False


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    file_id: UUID
    kind: str
    status: str
    text_method: str | None
    confidence: Decimal | None
    model: str | None
    prompt_version: str | None
    error_code: str | None
    error_detail: str | None
    created_by: UUID
    created_at: datetime
    completed_at: datetime | None
    draft_id: UUID | None = None


class ExtractionDetail(ExtractionOut):
    """``result.fields[]`` carry value, confidence, reasons and evidence (page, quote, word boxes) per pass."""

    result: dict[str, Any] | None
    validation: dict[str, Any] | None


class ExtractionList(BaseModel):
    data: list[ExtractionOut]


class UsageToday(BaseModel):
    cost_usd: str
    tokens: int
    cost_cap_usd: str
    token_cap: int


class UsageDay(BaseModel):
    date: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: str


class UsageOut(BaseModel):
    enabled: bool
    model: str
    today: UsageToday
    daily: list[UsageDay]


class MappingOut(BaseModel):
    mapping: dict[str, str | None]
    source: str
    model: str
    prompt_version: str


def _who(request: Request, principal: Principal) -> Who:
    return Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


def _out(db: Any, row: DocumentExtraction) -> ExtractionOut:
    out = ExtractionOut.model_validate(row)
    out.draft_id = service.draft_id_of(db, row.tenant_id, row.id)
    return out


@router.post(
    "/document-extractions",
    operation_id="extraction_start",
    status_code=202,
    responses={
        200: {"description": "An extraction of this file already exists"},
        409: {"description": "AI not enabled"},
    },
)
def start(
    request: Request,
    response: Response,
    body: ExtractionStart,
    principal: Annotated[Principal, Depends(require(DRAFT))],
) -> ExtractionOut:
    db = ctx(request).db
    if body.kind != "agreement":
        raise ValidationFailed(
            details=[{"field": "kind", "code": "invalid_choice", "message": "Only agreement is supported."}]
        )
    row, created = service.request_extraction(
        db, _who(request, principal), body.file_id, body.rerun, request.app.state.settings
    )
    response.status_code = 202 if created else 200
    return _out(db, row)


@router.get(
    "/document-extractions",
    operation_id="extraction_list",
    dependencies=[Depends(only_query_params("file_id", "limit"))],
)
def list_extractions(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    file_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ExtractionList:
    db = ctx(request).db
    rows = service.list_extractions(db, tenant_of(principal), file_id, limit)
    return ExtractionList(data=[_out(db, r) for r in rows])


@router.get("/document-extractions/{extraction_id}", operation_id="extraction_get")
def get_extraction(
    request: Request, extraction_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> ExtractionDetail:
    db = ctx(request).db
    row = service.get_extraction(db, tenant_of(principal), extraction_id)
    base = _out(db, row)
    return ExtractionDetail(**base.model_dump(), result=row.result, validation=row.validation)


@router.get("/ai/usage", operation_id="ai_usage", dependencies=[Depends(only_query_params())])
def ai_usage(request: Request, principal: Annotated[Principal, Depends(require(READ))]) -> UsageOut:
    c = ctx(request)
    return UsageOut(**service.usage(c.db, tenant_of(principal), request.app.state.settings))


@router.post(
    "/imports/{import_id}/suggest-mapping",
    operation_id="import_suggest_mapping",
    responses={409: {"description": "AI not enabled"}, 503: {"description": "AI unavailable"}},
)
def suggest_mapping(
    request: Request, import_id: UUID, principal: Annotated[Principal, Depends(require("ai.use"))]
) -> MappingOut:
    result = mapping.suggest_mapping(
        ctx(request).db, _who(request, principal), request.app.state.settings, import_id
    )
    return MappingOut(**result)
