"""The calculation job: one run per job, chunked inside (``runs.execute``).

Retries only make sense while another run holds the tenant lock (``RunInProgress``); every other outcome is
terminal and visible on the run (``failed`` + error details)."""

from uuid import UUID

from sqlalchemy.orm import Session

from rebate.core.jobs import JobContext, PermanentError, job
from rebate.modules.claims import runs, service


@job(service.JOB_KIND, tenant_scoped=True, max_attempts=8)
def calculation_run(db: Session, ctx: JobContext) -> None:
    if ctx.tenant_id is None or ctx.engine is None:  # unreachable: tenant-scoped, worker supplies the engine
        raise PermanentError("calculation runs need a tenant context and the worker engine")
    try:
        run_id = UUID(ctx.payload["run_id"])
    except KeyError, ValueError:
        raise PermanentError("calculation job without a valid run_id") from None
    runs.execute(ctx.engine, ctx.tenant_id, run_id, db)
