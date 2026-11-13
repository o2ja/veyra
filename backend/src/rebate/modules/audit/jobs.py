"""``audit_seal``: tenant-scoped, self-rescheduling (ADR-026). Each run seals what is due (bounded chunks),
then enqueues the next run: at once when more is due, else in an hour. The dedupe key embeds the run time, so
a chain is never started twice. The job writes no audit entry itself (it would feed its own chain)."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.jobs import JobContext, PermanentError, enqueue, job
from rebate.modules.audit import seal

KIND = "audit_seal"
INTERVAL = timedelta(hours=1)


def schedule(db: Session, tenant_id: UUID) -> None:
    """First run for a new workspace (existing ones are started by the migration)."""
    now = clock.now()
    enqueue(db, KIND, tenant_id=tenant_id, payload={}, dedupe_key=f"{KIND}:{tenant_id}:{now.isoformat()}")


@job(KIND, tenant_scoped=True)
def run(db: Session, ctx: JobContext) -> None:
    tenant = ctx.tenant_id
    if tenant is None:  # unreachable: the queue refuses a tenant-scoped job without a tenant
        raise PermanentError("tenant-scoped job without tenant context")
    now = clock.now()
    more = seal.seal_tenant(db, tenant, now)
    nxt = now if more else now + INTERVAL
    enqueue(
        db, KIND, tenant_id=tenant, payload={}, dedupe_key=f"{KIND}:{tenant}:{nxt.isoformat()}", run_at=nxt
    )
