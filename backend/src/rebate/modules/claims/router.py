"""Calculation runs, results, accruals, tracker, recalculation proposals and draft preview
(API_SPECIFICATION.md §2.8). Thin: validate, authorise, call the service, shape."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from rebate.core.access import require, tenant_of
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules import ingestion
from rebate.modules.claims import schemas as s
from rebate.modules.claims import service
from rebate.modules.masterdata import Who, page

router = APIRouter(tags=["calculation"])

READ = "data.read"
RUN = "calculation.run"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")


def _who(request: Request, principal: Principal) -> Who:
    return Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


@router.post(
    "/calculation-runs", status_code=202, operation_id="calculation_run_create", response_model=s.RunAccepted
)
def create_run(
    request: Request,
    body: s.RunIn,
    principal: Annotated[Principal, Depends(require(RUN))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    c, who = ctx(request), _who(request, principal)
    key = ingestion.parse_key(idempotency_key)
    payload = body.model_dump(mode="json")
    replayed = ingestion.replay(c.db, who.tenant_id, key, "POST /calculation-runs", payload)
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])
    run, job_id = service.create_run(
        c.db, who, list(body.manufacturer_ids), body.date_from, body.date_to, body.as_of
    )
    result = s.RunAccepted(
        job=s.JobRef(id=job_id, status="queued", progress=0, result_url=f"/api/v1/calculation-runs/{run.id}"),
        run=s.RunOut.model_validate(run),
    )
    ingestion.store(
        c.db, who.tenant_id, key, "POST /calculation-runs", payload, 202, result.model_dump(mode="json")
    )
    return result


@router.get(
    "/calculation-runs",
    operation_id="calculation_run_list",
    dependencies=[Depends(only_query_params("status", *PAGED))],
)
def list_runs(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    status: s.RunStatus | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.RunList:
    rows = service.list_runs(ctx(request).db, tenant_of(principal), status=status, limit=limit, cursor=cursor)
    data, p = page(rows, limit)
    return s.RunList(data=[s.RunOut.model_validate(r) for r in data], page=p)


@router.get("/calculation-runs/{run_id}", operation_id="calculation_run_get")
def get_run(
    request: Request, run_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.RunOut:
    return s.RunOut.model_validate(service.get_run(ctx(request).db, tenant_of(principal), run_id))


@router.get(
    "/calculation-runs/{run_id}/results",
    operation_id="calculation_result_list",
    dependencies=[
        Depends(only_query_params("status", "exception_code", "manufacturer_id", "agreement_id", *PAGED))
    ],
)
def list_results(
    request: Request,
    run_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    status: s.ResultStatus | None = None,
    exception_code: Annotated[str | None, Query(max_length=60)] = None,
    manufacturer_id: UUID | None = None,
    agreement_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ResultList:
    rows = service.list_results(
        ctx(request).db,
        tenant_of(principal),
        run_id,
        status=status,
        exception_code=exception_code,
        manufacturer_id=manufacturer_id,
        agreement_id=agreement_id,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.ResultList(data=[s.ResultOut(**r._mapping) for r in data], page=p)


@router.get("/calculation-results/{result_id}", operation_id="calculation_result_get")
def get_result(
    request: Request, result_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.ResultDetail:
    row = service.get_result(ctx(request).db, tenant_of(principal), result_id)
    return s.ResultDetail(**row._mapping, trace_ref={"kind": "calculation_result", "id": str(result_id)})


@router.get(
    "/rebate-accruals",
    operation_id="rebate_accrual_list",
    dependencies=[Depends(only_query_params("agreement_id", "run_id", *PAGED))],
)
def list_accruals(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    agreement_id: UUID | None = None,
    run_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.AccrualList:
    rows = service.list_accruals(
        ctx(request).db,
        tenant_of(principal),
        agreement_id=agreement_id,
        run_id=run_id,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.AccrualList(data=[s.AccrualOut(**r._mapping) for r in data], page=p)


@router.get("/rebate-tracker", operation_id="rebate_tracker_get", dependencies=[Depends(only_query_params())])
def tracker(request: Request, principal: Annotated[Principal, Depends(require(READ))]) -> s.TrackerOut:
    return s.TrackerOut(
        data=[s.TrackerRow(**r) for r in service.tracker(ctx(request).db, tenant_of(principal))]
    )


@router.post(
    "/calculation-runs/{run_id}/propose-adjustments", operation_id="calculation_run_propose_adjustments"
)
def propose_adjustments(
    request: Request, run_id: UUID, principal: Annotated[Principal, Depends(require(RUN))]
) -> s.ProposalsOut:
    compared, made = service.propose_adjustments(ctx(request).db, _who(request, principal), run_id)
    return s.ProposalsOut(
        run_id=run_id,
        compared=compared,
        proposed=[s.AdjustmentOut.model_validate(m, from_attributes=True) for m in made],
    )


@router.post(
    "/agreement-drafts/{draft_id}/preview-calculation", operation_id="agreement_draft_preview_calculation"
)
def preview_calculation(
    request: Request,
    draft_id: UUID,
    principal: Annotated[Principal, Depends(require(RUN))],
    body: s.PreviewIn | None = None,
) -> s.PreviewOut:
    out = service.preview(ctx(request).db, _who(request, principal), draft_id, (body or s.PreviewIn()).limit)
    return s.PreviewOut(**out)
