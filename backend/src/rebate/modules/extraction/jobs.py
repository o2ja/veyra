"""Job registration for S6: ``extract_agreement`` (tenant-scoped)."""

from sqlalchemy.orm import Session

from rebate.core.jobs import JobContext, job
from rebate.modules.extraction import service


@job(service.JOB, tenant_scoped=True, max_attempts=service.JOB_MAX_ATTEMPTS)
def extract_agreement(db: Session, ctx: JobContext) -> None:
    service.process(db, ctx)
