"""Jobs admin API (API_SPECIFICATION.md §2.10). The queue table is global (ADR-005): every query here
filters ``tenant_id`` to the caller's workspace explicitly; jobs of other workspaces, and system jobs
without a workspace (identity emails), are never visible through the API."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from rebate.core import jobs
from rebate.core.access import require, tenant_of
from rebate.core.errors import InvalidState, NotFound
from rebate.core.jobs import Job
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules import audit
from rebate.modules.audit import Action, Actor

router = APIRouter(tags=["jobs"])


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    run_at: datetime
    finished_at: datetime | None


class JobList(BaseModel):
    data: list[JobOut]


def _job(request: Request, tenant_id: UUID, job_id: UUID, *, lock: bool = False) -> Job:
    stmt = select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id)
    row = ctx(request).db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


@router.get("/jobs", operation_id="job_list", dependencies=[Depends(only_query_params("status", "limit"))])
def list_jobs(
    request: Request,
    principal: Annotated[Principal, Depends(require("jobs.manage"))],
    status: Literal["queued", "running", "succeeded", "failed"] = "failed",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobList:
    rows = ctx(request).db.scalars(
        select(Job)
        .where(Job.tenant_id == tenant_of(principal), Job.status == status)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return JobList(data=[JobOut.model_validate(r) for r in rows])


@router.get("/jobs/{job_id}", operation_id="job_get")
def get_job(
    request: Request, job_id: UUID, principal: Annotated[Principal, Depends(require("data.read"))]
) -> JobOut:
    """Creator or admin (API_SPECIFICATION.md §2.10); others get 404 like a missing job."""
    row = _job(request, tenant_of(principal), job_id)
    if row.created_by != principal.user_id and "jobs.manage" not in principal.permissions:
        raise NotFound()
    return JobOut.model_validate(row)


@router.post("/jobs/{job_id}/retry", operation_id="job_retry")
def retry_job(
    request: Request, job_id: UUID, principal: Annotated[Principal, Depends(require("jobs.manage"))]
) -> JobOut:
    c = ctx(request)
    tenant_id = tenant_of(principal)
    row = _job(request, tenant_id, job_id, lock=True)
    before = {"kind": row.kind, "status": row.status, "attempts": row.attempts}
    if not jobs.retry(c.db, row.id):
        raise InvalidState("Only failed jobs can be retried.")
    c.db.refresh(row)
    audit.record(
        c.db,
        tenant_id=tenant_id,
        actor=Actor("user", principal.user_id),
        action=Action.JOB_RETRIED,
        entity_type="job",
        entity_id=row.id,
        before=before,
        after={"kind": row.kind, "status": row.status, "attempts": row.attempts},
        meta=c.audit_meta(),
    )
    return JobOut.model_validate(row)
