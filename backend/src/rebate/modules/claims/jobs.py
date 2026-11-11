"""The calculation job: one run per job, chunked inside (``runs.execute``).

* Waiting for the tenant lock never burns attempts: the job ends and a fresh one is queued ``WAIT`` later.
* Any other terminal outcome is recorded on the run (``failed`` + error details). When the job itself dies (attempts
  exhausted, worker lost on the last attempt) ``on_dead`` fails a run that is still queued or running, so a scope is
  never blocked forever. A run left ``running`` by a crashed worker is failed (``stale_run``) by the next attempt."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.jobs import JobContext, PermanentError, enqueue, job
from rebate.modules import audit
from rebate.modules.claims import runs, service
from rebate.modules.claims.models import CalculationRun

WAIT = timedelta(seconds=30)


def _run_id(ctx: JobContext) -> UUID:
    try:
        return UUID(ctx.payload["run_id"])
    except KeyError, ValueError:
        raise PermanentError("calculation job without a valid run_id") from None


def _job_died(db: Session, ctx: JobContext) -> None:
    if ctx.tenant_id is None:
        return
    run_id = _run_id(ctx)
    details = [runs.detail("job", "job_failed", "The calculation job failed permanently; start a new run.")]
    failed = db.execute(
        update(CalculationRun)
        .where(
            CalculationRun.tenant_id == ctx.tenant_id,
            CalculationRun.id == run_id,
            CalculationRun.status.in_(("queued", "running")),
        )
        .values(status="failed", error={"details": details}, completed_at=clock.now())
        .returning(CalculationRun.id)
    ).first()
    if failed is not None:
        audit.record(
            db,
            tenant_id=ctx.tenant_id,
            actor=audit.Actor("system"),
            action=audit.Action.CALCULATION_RUN_FAILED,
            entity_type="calculation_run",
            entity_id=run_id,
            after={"status": "failed", "error": {"details": details}},
        )


@job(service.JOB_KIND, tenant_scoped=True, max_attempts=5, on_dead=_job_died)
def calculation_run(db: Session, ctx: JobContext) -> None:
    if ctx.tenant_id is None or ctx.engine is None:  # unreachable: tenant-scoped, worker supplies the engine
        raise PermanentError("calculation runs need a tenant context and the worker engine")
    run_id = _run_id(ctx)
    try:
        runs.execute(ctx.engine, ctx.tenant_id, run_id, db, heartbeat=ctx.renew_lease)
    except runs.RunInProgress:
        enqueue(
            db,
            service.JOB_KIND,
            tenant_id=ctx.tenant_id,
            payload={"run_id": str(run_id)},
            created_by=ctx.created_by,
            dedupe_key=f"{service.JOB_KIND}:{run_id}:wait:{ctx.id}",
            run_at=clock.now() + WAIT,
        )
