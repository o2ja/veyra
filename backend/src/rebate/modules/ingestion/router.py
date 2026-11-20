"""Imports, mapping templates and source-line API (API_SPECIFICATION.md §2.4). Thin router."""

import csv
import io
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from rebate.core.access import require, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules.ingestion import idempotency, lines, service
from rebate.modules.ingestion import schemas as s
from rebate.modules.ingestion.models import ImportBatch
from rebate.modules.masterdata import Page, page

router = APIRouter(tags=["imports"])

READ = "data.read"
RUN = "import.run"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")


def _out(b: ImportBatch) -> s.ImportOut:
    return s.ImportOut(
        id=b.id,
        import_type=b.import_type,
        file_id=b.file_id,
        mapping_template_id=b.mapping_template_id,
        mapping_template_version=b.mapping_template_version,
        source_system=b.source_system,
        status=b.status,
        accept_partial=b.accept_partial,
        counts=s.ImportCounts(**service.counts_of(b)),
        error_summary=b.error_summary,
        detected_encoding=b.detected_encoding,
        delimiter=b.delimiter,
        sheet_name=b.sheet_name,
        header_row=b.header_row,
        headers=b.headers,
        mapping=b.mapping,
        options=b.options or {},
        mapping_revision=b.mapping_revision,
        needs_mapping=b.status == "awaiting_confirmation" and b.mapping is None,
        created_by=b.created_by,
        created_at=b.created_at,
        started_at=b.started_at,
        confirmed_by=b.confirmed_by,
        confirmed_at=b.confirmed_at,
        completed_at=b.completed_at,
    )


def _accepted(b: ImportBatch, job_id: UUID) -> s.ImportAccepted:
    job = s.JobRef(
        id=job_id, status="queued", progress=service.job_progress(b), result_url=f"/api/v1/imports/{b.id}"
    )
    return s.ImportAccepted(job=job, import_=_out(b))


@router.post("/imports", status_code=202, operation_id="import_create", response_model=s.ImportAccepted)
def create_import(
    request: Request,
    body: s.ImportIn,
    principal: Annotated[Principal, Depends(require(RUN))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    c, tenant_id = ctx(request), tenant_of(principal)
    key = idempotency.parse_key(idempotency_key)
    payload = body.model_dump(mode="json")
    replayed = idempotency.replay(c.db, tenant_id, key, "POST /imports", payload)
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])
    batch, job_id = service.create_import(
        c.db,
        tenant_id=tenant_id,
        user_id=principal.user_id,
        file_id=body.file_id,
        import_type=body.import_type,
        mapping_template_id=body.mapping_template_id,
        source_system=body.source_system,
        meta=c.audit_meta(),
    )
    result = _accepted(batch, job_id)
    idempotency.store(
        c.db, tenant_id, key, "POST /imports", payload, 202, result.model_dump(mode="json", by_alias=True)
    )
    return result


@router.get(
    "/imports",
    operation_id="import_list",
    dependencies=[Depends(only_query_params("status", "import_type", *PAGED))],
)
def list_imports(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    status: s.ImportStatus | None = None,
    import_type: s.ImportType | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ImportList:
    rows = service.list_batches(
        ctx(request).db,
        tenant_of(principal),
        status=status,
        import_type=import_type,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.ImportList(data=[_out(b) for b in data], page=p)


@router.get("/imports/{import_id}", operation_id="import_get")
def get_import(
    request: Request, import_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.ImportOut:
    return _out(service.get_batch(ctx(request).db, tenant_of(principal), import_id))


@router.get("/imports/{import_id}/preview", operation_id="import_preview", response_model=s.PreviewOut)
def preview(
    request: Request, import_id: UUID, principal: Annotated[Principal, Depends(require(RUN))]
) -> s.PreviewOut:
    db = ctx(request).db
    batch = service.get_batch(db, tenant_of(principal), import_id)
    return s.PreviewOut.model_validate({"import": _out(batch), **service.preview(db, batch)})


@router.put(
    "/imports/{import_id}/mapping",
    status_code=202,
    operation_id="import_set_mapping",
    response_model=s.ImportAccepted,
)
def set_mapping(
    request: Request,
    import_id: UUID,
    body: s.MappingIn,
    principal: Annotated[Principal, Depends(require(RUN))],
) -> s.ImportAccepted:
    c = ctx(request)
    batch, job_id = service.set_mapping(
        c.db,
        tenant_id=tenant_of(principal),
        user_id=principal.user_id,
        batch_id=import_id,
        mapping=body.mapping,
        options=body.options.model_dump(),
        template_name=body.save_as_template.name if body.save_as_template else None,
        meta=c.audit_meta(),
    )
    return _accepted(batch, job_id)


@router.post(
    "/imports/{import_id}/confirm",
    status_code=202,
    operation_id="import_confirm",
    response_model=s.ImportAccepted,
)
def confirm(
    request: Request,
    import_id: UUID,
    body: s.ConfirmIn,
    principal: Annotated[Principal, Depends(require(RUN))],
) -> s.ImportAccepted:
    c = ctx(request)
    batch, job_id = service.confirm(
        c.db,
        tenant_id=tenant_of(principal),
        user_id=principal.user_id,
        batch_id=import_id,
        accept_partial=body.accept_partial,
        meta=c.audit_meta(),
    )
    return _accepted(batch, job_id)


@router.post("/imports/{import_id}/discard", operation_id="import_discard")
def discard(
    request: Request, import_id: UUID, principal: Annotated[Principal, Depends(require(RUN))]
) -> s.ImportOut:
    c = ctx(request)
    batch = service.discard(
        c.db,
        tenant_id=tenant_of(principal),
        user_id=principal.user_id,
        batch_id=import_id,
        meta=c.audit_meta(),
    )
    return _out(batch)


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    """CSV export cell with formula-injection protection (SECURITY.md §4)."""
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip(" ").startswith(FORMULA_START) else text


def _csv_lines(rows: list[tuple[Any, ...]]) -> Iterator[str]:
    """Rows are read inside the request transaction; only the formatting is streamed."""
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["row_number", "column", "severity", "code", "message"])
    for i, row in enumerate(rows, 1):
        out.writerow([_cell(v) for v in row])
        if i % 1000 == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    yield buf.getvalue()


@router.get(
    "/imports/{import_id}/errors",
    operation_id="import_errors",
    response_model=s.RowErrorList,
    dependencies=[Depends(only_query_params("format", *PAGED))],
    responses={200: {"content": {"text/csv": {}}}},
)
def errors(
    request: Request,
    import_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    format: Literal["json", "csv"] = "json",
    limit: Limit = 100,
    cursor: str | None = None,
) -> Any:
    c = ctx(request)
    db, tenant_id = c.db, tenant_of(principal)
    if format == "csv":
        rows, truncated = service.export_errors(
            db, tenant_id, import_id, user_id=principal.user_id, meta=c.audit_meta()
        )
        return StreamingResponse(
            _csv_lines(rows),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="import-{import_id}-errors.csv"',
                "X-Export-Truncated": "true" if truncated else "false",
            },
        )
    found = service.list_errors(db, tenant_id, import_id, limit=limit, cursor=cursor)
    data, p = page(found, limit)
    return s.RowErrorList(data=[s.RowErrorOut.model_validate(e) for e in data], page=p)


@router.get(
    "/imports/{import_id}/changes",
    operation_id="import_changes",
    dependencies=[Depends(only_query_params(*PAGED))],
)
def changes(
    request: Request,
    import_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 100,
    cursor: str | None = None,
) -> s.ChangeList:
    db, tenant_id = ctx(request).db, tenant_of(principal)
    batch = service.get_batch(db, tenant_id, import_id)
    if batch.import_type not in ("sales", "purchases"):
        return s.ChangeList(data=[], page=Page(next_cursor=None, has_more=False))
    found = lines.changes(db, tenant_id, batch, limit, cursor)
    data, p = page(found, limit, id_of=lambda t: t[0].id)
    return s.ChangeList(
        data=[
            s.ChangeOut(
                line_id=new.id,
                previous_line_id=old.id,
                previous_import_batch_id=old.import_batch_id,
                document_number=new.document_number,
                line_number=new.line_number,
                source_row_number=new.source_row_number,
                version=new.version,
                source_changed=new.source_changed,
                changed_fields=diff,
            )
            for new, old, diff in data
        ],
        page=p,
    )


# ---- mapping templates --------------------------------------------------------------------------


def _version(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the template version you edited.")
    return int(value)


@router.get(
    "/mapping-templates",
    operation_id="mapping_template_list",
    dependencies=[Depends(only_query_params("import_type", *PAGED))],
)
def list_templates(
    request: Request,
    principal: Annotated[Principal, Depends(require("mapping.write"))],
    import_type: s.TemplateType | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.TemplateList:
    rows = service.list_templates(
        ctx(request).db, tenant_of(principal), import_type=import_type, limit=limit, cursor=cursor
    )
    data, p = page(rows, limit)
    return s.TemplateList(data=[s.TemplateOut.model_validate(t) for t in data], page=p)


@router.post("/mapping-templates", status_code=201, operation_id="mapping_template_create")
def create_template(
    request: Request,
    response: Response,
    body: s.TemplateIn,
    principal: Annotated[Principal, Depends(require("mapping.write"))],
) -> s.TemplateOut:
    c = ctx(request)
    row = service.save_template(
        c.db,
        tenant_id=tenant_of(principal),
        user_id=principal.user_id,
        name=body.name,
        import_type=body.import_type,
        headers=body.headers,
        mapping=body.mapping,
        options=body.options.model_dump(),
        meta=c.audit_meta(),
    )
    response.headers["ETag"] = f'"{row.version}"'
    return s.TemplateOut.model_validate(row)


@router.get("/mapping-templates/{template_id}", operation_id="mapping_template_get")
def get_template(
    request: Request,
    response: Response,
    template_id: UUID,
    principal: Annotated[Principal, Depends(require("mapping.write"))],
) -> s.TemplateOut:
    row = service.get_template(ctx(request).db, tenant_of(principal), template_id)
    response.headers["ETag"] = f'"{row.version}"'
    return s.TemplateOut.model_validate(row)


@router.put(
    "/mapping-templates/{template_id}",
    operation_id="mapping_template_update",
    responses={412: {"description": "Version mismatch"}},
)
def update_template(
    request: Request,
    response: Response,
    template_id: UUID,
    body: s.TemplateUpdate,
    principal: Annotated[Principal, Depends(require("mapping.write"))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.TemplateOut:
    c = ctx(request)
    row = service.update_template(
        c.db,
        tenant_id=tenant_of(principal),
        user_id=principal.user_id,
        template_id=template_id,
        expected_version=_version(if_match),
        headers=body.headers,
        mapping=body.mapping,
        options=body.options.model_dump(),
        meta=c.audit_meta(),
    )
    response.headers["ETag"] = f'"{row.version}"'
    return s.TemplateOut.model_validate(row)


@router.delete("/mapping-templates/{template_id}", status_code=204, operation_id="mapping_template_delete")
def delete_template(
    request: Request, template_id: UUID, principal: Annotated[Principal, Depends(require("mapping.write"))]
) -> Response:
    c = ctx(request)
    service.delete_template(
        c.db,
        tenant_id=tenant_of(principal),
        user_id=principal.user_id,
        template_id=template_id,
        meta=c.audit_meta(),
    )
    return Response(status_code=204)


# ---- source lines -------------------------------------------------------------------------------

LINE_FILTERS = (
    "manufacturer_id", "customer_id", "product_id", "import_batch_id", "document_number", "doc_type",
    "date_from", "date_to", "current",
)  # fmt: skip


def _lineage(line: Any, file_id: UUID) -> s.Lineage:
    return s.Lineage(
        import_batch_id=line.import_batch_id,
        file_id=file_id,
        source_row_number=line.source_row_number,
        source_system=line.source_system,
        version=line.version,
        is_current=line.is_current,
        superseded_by_id=line.superseded_by_id,
    )


def _sales_out(line: Any, file_id: UUID) -> dict[str, Any]:
    fields = s.SalesLineOut.model_fields.keys() - {"lineage"}
    return {**{k: getattr(line, k) for k in fields}, "lineage": _lineage(line, file_id)}


@router.get(
    "/sales-lines",
    operation_id="sales_line_list",
    dependencies=[Depends(only_query_params(*LINE_FILTERS, *PAGED))],
)
def list_sales_lines(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    manufacturer_id: UUID | None = None,
    customer_id: UUID | None = None,
    product_id: UUID | None = None,
    import_batch_id: UUID | None = None,
    document_number: Annotated[str | None, Query(max_length=100)] = None,
    doc_type: Literal["invoice", "credit_note"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    current: bool | None = True,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.SalesLineList:
    f = lines.LineFilter(
        manufacturer_id,
        customer_id,
        product_id,
        import_batch_id,
        document_number,
        doc_type,
        date_from,
        date_to,
        current,
    )
    found = lines.sales_lines(ctx(request).db, tenant_of(principal), f, limit, cursor)
    data, p = page(found, limit, id_of=lambda t: t[0].id)
    return s.SalesLineList(
        data=[s.SalesLineOut.model_validate(_sales_out(line, fid)) for line, fid in data], page=p
    )


@router.get("/sales-lines/{line_id}", operation_id="sales_line_get")
def get_sales_line(
    request: Request, line_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.SalesLineDetail:
    line, file_id, previous = lines.sales_line(ctx(request).db, tenant_of(principal), line_id)
    return s.SalesLineDetail.model_validate({**_sales_out(line, file_id), "previous_version_id": previous})


@router.get(
    "/purchase-lines",
    operation_id="purchase_line_list",
    dependencies=[Depends(only_query_params(*(x for x in LINE_FILTERS if x != "customer_id"), *PAGED))],
)
def list_purchase_lines(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    manufacturer_id: UUID | None = None,
    product_id: UUID | None = None,
    import_batch_id: UUID | None = None,
    document_number: Annotated[str | None, Query(max_length=100)] = None,
    doc_type: Literal["receipt", "vendor_credit"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    current: bool | None = True,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.PurchaseLineList:
    f = lines.LineFilter(
        manufacturer_id,
        None,
        product_id,
        import_batch_id,
        document_number,
        doc_type,
        date_from,
        date_to,
        current,
    )
    found = lines.purchase_lines(ctx(request).db, tenant_of(principal), f, limit, cursor)
    data, p = page(found, limit, id_of=lambda t: t[0].id)
    fields = s.PurchaseLineOut.model_fields.keys() - {"lineage"}
    return s.PurchaseLineList(
        data=[
            s.PurchaseLineOut.model_validate(
                {**{k: getattr(line, k) for k in fields}, "lineage": _lineage(line, fid)}
            )
            for line, fid in data
        ],
        page=p,
    )
