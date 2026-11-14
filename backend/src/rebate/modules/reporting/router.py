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
from rebate.modules.masterdata import Who, decode_cursor, page
from rebate.modules.reporting import jobs, service
from rebate.modules.reporting import schemas as s

router = APIRouter(tags=["reporting"])

READ = "report.read"
FILTERS = ("manufacturer_id", "branch_id", "month")
Limit = Annotated[int, Query(ge=1, le=200)]
NO_QUERY = Depends(only_query_params())


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


def _trace(request: Request, principal: Principal, kind: s.TraceKind, ident: UUID) -> s.TraceOut:
    chain = service.trace(ctx(request).db, tenant_of(principal), kind, ident)
    return s.TraceOut(kind=kind, id=ident, chain=[s.TraceNode.model_validate(n) for n in chain])


# One route per kind (``/trace/{kind}/{id}`` of the API spec, with the kind fixed in the path).
@router.get("/trace/claim_line/{claim_line_id}", operation_id="trace_claim_line", dependencies=[NO_QUERY])
def trace_claim_line(
    request: Request, claim_line_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.TraceOut:
    return _trace(request, principal, "claim_line", claim_line_id)


@router.get(
    "/trace/claim_adjustment/{adjustment_id}", operation_id="trace_claim_adjustment", dependencies=[NO_QUERY]
)
def trace_claim_adjustment(
    request: Request, adjustment_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.TraceOut:
    return _trace(request, principal, "claim_adjustment", adjustment_id)


@router.get(
    "/trace/remittance_line/{remittance_line_id}",
    operation_id="trace_remittance_line",
    dependencies=[NO_QUERY],
)
def trace_remittance_line(
    request: Request, remittance_line_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.TraceOut:
    return _trace(request, principal, "remittance_line", remittance_line_id)


@router.get("/trace/variance_case/{case_id}", operation_id="trace_variance_case", dependencies=[NO_QUERY])
def trace_variance_case(
    request: Request, case_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.TraceOut:
    return _trace(request, principal, "variance_case", case_id)


@router.get(
    "/trace/calculation_result/{result_id}", operation_id="trace_calculation_result", dependencies=[NO_QUERY]
)
def trace_calculation_result(
    request: Request, result_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.TraceOut:
    return _trace(request, principal, "calculation_result", result_id)


@router.post(
    "/reports/claim-batches/{batch_id}/audit-pack", status_code=202, operation_id="claim_batch_audit_pack"
)
def audit_pack(
    request: Request,
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require("audit_pack.export"))],
    body: s.AuditPackIn | None = None,
) -> JSONResponse:
    c = ctx(request)
    who = Who(tenant_of(principal), principal.user_id, c.audit_meta())
    file_id, job_id = jobs.request_audit_pack(c.db, who, batch_id, (body or s.AuditPackIn()).format)
    body_out = s.AuditPackAccepted(
        job=RunJobRef(id=job_id, status="queued", progress=0, result_url=f"/api/v1/files/{file_id}"),
        file_id=file_id,
    )
    return JSONResponse(body_out.model_dump(mode="json"), 202)
