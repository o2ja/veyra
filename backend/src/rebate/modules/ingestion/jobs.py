"""ingestion jobs (ARCHITECTURE.md §6): ``import_parse`` and ``import_load``. Thin: call the service.

A dead letter marks the batch ``failed`` (``on_dead``); an admin retry of the job continues from the
committed state (parse restarts, load resumes from the checkpoint)."""

from sqlalchemy.orm import Session

from rebate.core.jobs import JobContext, job
from rebate.modules.ingestion import service


@job("import_parse", tenant_scoped=True, max_attempts=3, on_dead=service.dead)
def import_parse(db: Session, ctx: JobContext) -> None:
    service.parse(db, ctx)


@job("import_load", tenant_scoped=True, max_attempts=5, on_dead=service.dead)
def import_load(db: Session, ctx: JobContext) -> None:
    service.load_chunk(db, ctx)
