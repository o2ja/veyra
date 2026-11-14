"""Missed claims, claim export templates and claim batches (API_SPECIFICATION.md §2.8).
Thin: validate, authorise, call the service, shape. Permissions (SECURITY.md §3): reads ``data.read``; templates and
batch creation ``claim_batch.create``; approve/export/submit/void their own. ``POST /claim-batches`` and
``…/submit`` need an ``Idempotency-Key``; template edits need ``If-Match: "<revision>"``."""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from rebate.core.access import require, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules import ingestion
from rebate.modules.claims import batch_schemas as s
from rebate.modules.claims import batches, missed, templates
from rebate.modules.claims.schemas import RunJobRef
from rebate.modules.masterdata import Who, decode_cursor, page

router = APIRouter(tags=["claims"])

READ = "data.read"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")
MISSED_FILTERS = ("manufacturer_id", "agreement_id", "invoice_from", "invoice_to")


def _who(request: Request, principal: Principal) -> Who:
    return Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


def _revision(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the revision you edited.")
    return int(value)


def _accepted(file_id: UUID, job_id: UUID) -> JSONResponse:
    body = s.ExportAccepted(
        job=RunJobRef(id=job_id, status="queued", progress=0, result_url=f"/api/v1/files/{file_id}"),
        file_id=file_id,
    )
    return JSONResponse(body.model_dump(mode="json"), 202)


# ---- missed claims ------------------------------------------------------------------------------------------


@router.get(
    "/missed-claims",
    operation_id="missed_claim_list",
    dependencies=[Depends(only_query_params(*MISSED_FILTERS, "format", *PAGED))],
    response_model=s.MissedList,
    responses={202: {"model": s.ExportAccepted}},
)
def list_missed(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    manufacturer_id: UUID | None = None,
    agreement_id: UUID | None = None,
    invoice_from: date | None = None,
    invoice_to: date | None = None,
    format: s.ExportFormat | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Any:
    c, who = ctx(request), _who(request, principal)
    if format is not None:
        filt = {
            k: str(v)
            for k, v in (
                ("manufacturer_id", manufacturer_id),
                ("agreement_id", agreement_id),
                ("invoice_from", invoice_from),
                ("invoice_to", invoice_to),
            )
            if v is not None
        }
        return _accepted(*batches.request_missed_export(c.db, who, format, filt))
    rows = missed.missed(
        c.db,
        who.tenant_id,
        manufacturer_id=manufacturer_id,
        agreement_id=agreement_id,
        invoice_from=invoice_from,
        invoice_to=invoice_to,
        after=decode_cursor(cursor),
        limit=limit + 1,
    )
    data, p = page(rows, limit)
    return s.MissedList(data=[s.MissedOut(**r._mapping) for r in data], page=p)


@router.get(
    "/missed-claims/summary",
    operation_id="missed_claim_summary",
    dependencies=[Depends(only_query_params(*MISSED_FILTERS))],
)
def missed_summary(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    manufacturer_id: UUID | None = None,
    agreement_id: UUID | None = None,
    invoice_from: date | None = None,
    invoice_to: date | None = None,
) -> s.MissedSummary:
    return s.MissedSummary(
        **missed.summary(
            ctx(request).db,
            tenant_of(principal),
            manufacturer_id=manufacturer_id,
            agreement_id=agreement_id,
            invoice_from=invoice_from,
            invoice_to=invoice_to,
        )
    )


# ---- export templates ---------------------------------------------------------------------------------------

TEMPLATE = "claim_batch.create"


@router.get(
    "/claim-export-templates",
    operation_id="claim_export_template_list",
    dependencies=[Depends(only_query_params("manufacturer_id", "active", *PAGED))],
)
def list_templates(
    request: Request,
    principal: Annotated[Principal, Depends(require(TEMPLATE))],
    manufacturer_id: UUID | None = None,
    active: bool | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ExportTemplateList:
    rows = templates.list_templates(
        ctx(request).db,
        tenant_of(principal),
        manufacturer_id=manufacturer_id,
        active=active,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.ExportTemplateList(data=[s.ExportTemplateOut.model_validate(r) for r in data], page=p)


@router.post("/claim-export-templates", status_code=201, operation_id="claim_export_template_create")
def create_template(
    request: Request,
    body: s.ExportTemplateCreate,
    response: Response,
    principal: Annotated[Principal, Depends(require(TEMPLATE))],
) -> s.ExportTemplateOut:
    row = templates.create(ctx(request).db, _who(request, principal), body)
    response.headers["ETag"] = f'"{row.revision}"'
    return s.ExportTemplateOut.model_validate(row)


@router.put("/claim-export-templates/{export_template_id}", operation_id="claim_export_template_replace")
def replace_template(
    request: Request,
    export_template_id: UUID,
    body: s.ExportTemplateBody,
    response: Response,
    principal: Annotated[Principal, Depends(require(TEMPLATE))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.ExportTemplateOut:
    row = templates.replace(
        ctx(request).db, _who(request, principal), export_template_id, _revision(if_match), body
    )
    response.headers["ETag"] = f'"{row.revision}"'
    return s.ExportTemplateOut.model_validate(row)


# ---- batches ------------------------------------------------------------------------------------------------


@router.post("/claim-batches", status_code=201, operation_id="claim_batch_create", response_model=s.BatchOut)
def create_batch(
    request: Request,
    body: s.BatchIn,
    principal: Annotated[Principal, Depends(require("claim_batch.create"))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    c, who = ctx(request), _who(request, principal)
    key = ingestion.parse_key(idempotency_key)
    payload = body.model_dump(mode="json")
    replayed = ingestion.replay(c.db, who.tenant_id, key, "POST /claim-batches", payload)
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])
    out = s.BatchOut.model_validate(batches.create(c.db, who, body))
    ingestion.store(
        c.db, who.tenant_id, key, "POST /claim-batches", payload, 201, out.model_dump(mode="json")
    )
    return out


@router.get(
    "/claim-batches",
    operation_id="claim_batch_list",
    dependencies=[Depends(only_query_params("status", "manufacturer_id", *PAGED))],
)
def list_batches(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    status: s.BatchStatus | None = None,
    manufacturer_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.BatchList:
    rows = batches.list_batches(
        ctx(request).db,
        tenant_of(principal),
        status=status,
        manufacturer_id=manufacturer_id,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.BatchList(data=[s.BatchOut.model_validate(r) for r in data], page=p)


@router.get("/claim-batches/{batch_id}", operation_id="claim_batch_get")
def get_batch(
    request: Request, batch_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.BatchOut:
    return s.BatchOut.model_validate(batches.get(ctx(request).db, tenant_of(principal), batch_id))


@router.get(
    "/claim-batches/{batch_id}/lines",
    operation_id="claim_batch_line_list",
    dependencies=[Depends(only_query_params(*PAGED))],
)
def list_batch_lines(
    request: Request,
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.LineList:
    rows = batches.list_lines(ctx(request).db, tenant_of(principal), batch_id, limit=limit, cursor=cursor)
    data, p = page(rows, limit)
    return s.LineList(data=[s.LineOut(**r._mapping) for r in data], page=p)


@router.post("/claim-batches/{batch_id}/approve", operation_id="claim_batch_approve")
def approve_batch(
    request: Request, batch_id: UUID, principal: Annotated[Principal, Depends(require("claim_batch.approve"))]
) -> s.BatchOut:
    return s.BatchOut.model_validate(batches.approve(ctx(request).db, _who(request, principal), batch_id))


@router.post("/claim-batches/{batch_id}/export", status_code=202, operation_id="claim_batch_export")
def export_batch(
    request: Request,
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require("claim_batch.export"))],
    body: s.BatchExportIn | None = None,
) -> JSONResponse:
    template_id = (body or s.BatchExportIn()).template_id
    return _accepted(
        *batches.request_batch_export(ctx(request).db, _who(request, principal), batch_id, template_id)
    )


@router.post("/claim-batches/{batch_id}/submit", operation_id="claim_batch_submit", response_model=s.BatchOut)
def submit_batch(
    request: Request,
    batch_id: UUID,
    body: s.BatchSubmitIn,
    principal: Annotated[Principal, Depends(require("claim_batch.submit"))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    c, who = ctx(request), _who(request, principal)
    key = ingestion.parse_key(idempotency_key)
    endpoint = f"POST /claim-batches/{batch_id}/submit"
    payload = body.model_dump(mode="json")
    replayed = ingestion.replay(c.db, who.tenant_id, key, endpoint, payload)
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])
    out = s.BatchOut.model_validate(
        batches.submit(c.db, who, batch_id, body.submitted_at, body.external_reference)
    )
    ingestion.store(c.db, who.tenant_id, key, endpoint, payload, 200, out.model_dump(mode="json"))
    return out


@router.post("/claim-batches/{batch_id}/void", operation_id="claim_batch_void")
def void_batch(
    request: Request,
    batch_id: UUID,
    body: s.BatchVoidIn,
    principal: Annotated[Principal, Depends(require("claim_batch.void"))],
) -> s.BatchOut:
    return s.BatchOut.model_validate(
        batches.void(ctx(request).db, _who(request, principal), batch_id, body.reason)
    )
