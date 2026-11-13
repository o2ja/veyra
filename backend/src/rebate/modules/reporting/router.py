"""Dashboard, drill-down, trace resolver and audit pack (API_SPECIFICATION.md §2.9). Thin: validate, authorise, call
the service, shape."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from rebate.core.access import require, tenant_of
from rebate.core.figures import Filter
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules.claims import RunJobRef
from rebate.modules.masterdata import decode_cursor, page
from rebate.modules.reporting import jobs, service
from rebate.modules.reporting import schemas as s

router = APIRouter(tags=["reporting"])

READ = "report.read"
FILTERS = ("manufacturer_id", "branch_id", "month")
Limit = Annotated[int, Query(ge=1, le=200)]


def _filter(manufacturer_id: UUID | None, branch_id: UUID | None, month: date | None) -> Filter:
    return Filter(manufacturer_id, branch_id, month.replace(day=1) if month else None)


@router.get("/dashboard", operation_id="dashboard_get", dependencies=[Depends(only_query_params(*FILTERS))])
def get_dashboard(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    manufacturer_id: UUID | None = None,
    branch_id: UUID | None = None,
    month: date | None = None,
) -> s.DashboardOut:
    out = service.dashboard(ctx(request).db, tenant_of(principal), _filter(manufacturer_id, branch_id, month))
    return s.DashboardOut.model_validate(out)


@router.post("/dashboard/refresh", status_code=202, operation_id="dashboard_refresh")
def refresh_dashboard(
    request: Request, principal: Annotated[Principal, Depends(require("report.refresh"))]
) -> s.RefreshAccepted:
    return s.RefreshAccepted(job_id=jobs.request_refresh(ctx(request).db, tenant_of(principal)))


@router.get(
    "/dashboard/drilldown",
    operation_id="dashboard_drilldown",
    dependencies=[Depends(only_query_params("metric", *FILTERS, "limit", "cursor"))],
)
def get_drilldown(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    metric: s.Metric,
    manufacturer_id: UUID | None = None,
    branch_id: UUID | None = None,
    month: date | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.DrilldownOut:
    out = service.drilldown(
        ctx(request).db,
        tenant_of(principal),
        metric,
        _filter(manufacturer_id, branch_id, month),
        decode_cursor(cursor),
        limit,
    )
    data, p = page(out.pop("rows"), limit)
    return s.DrilldownOut(**out, data=[s.DrillRow.model_validate(r._mapping) for r in data], page=p)


@router.get("/trace/{kind}/{ident}", operation_id="trace_get", dependencies=[Depends(only_query_params())])
def get_trace(
    request: Request,
    kind: s.TraceKind,
    ident: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
) -> s.TraceOut:
    chain = service.trace(ctx(request).db, tenant_of(principal), kind, ident)
    return s.TraceOut(kind=kind, id=ident, chain=[s.TraceNode.model_validate(n) for n in chain])


@router.get(
    "/reports/claim-batches/{batch_id}/audit-pack",
    status_code=202,
    operation_id="claim_batch_audit_pack",
    dependencies=[Depends(only_query_params("format"))],
)
def audit_pack(
    request: Request,
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require("audit_pack.export"))],
    format: s.ExportFormat = "csv",
) -> JSONResponse:
    c = ctx(request)
    file_id, job_id = jobs.request_audit_pack(c.db, tenant_of(principal), principal.user_id, batch_id, format)
    body = s.AuditPackAccepted(
        job=RunJobRef(id=job_id, status="queued", progress=0, result_url=f"/api/v1/files/{file_id}"),
        file_id=file_id,
    )
    return JSONResponse(body.model_dump(mode="json"), 202)
