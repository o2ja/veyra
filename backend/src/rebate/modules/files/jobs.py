"""files jobs: malware scan with quarantine -> clean promotion; admin notification on infection.

``files_scan`` is idempotent: a file that already has a final outcome is left alone. A clamd outage
is transient (retried with backoff); when retries run out, ``on_dead`` marks the file ``error`` so it
can never be used, and the admin can retry the job from the jobs screen.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core import clock, jobs
from rebate.core.jobs import JobContext, PermanentError, job
from rebate.core.mail import Mail, deliver
from rebate.modules import audit, identity
from rebate.modules.audit import Action, Actor
from rebate.modules.files import scanner, service, storage
from rebate.modules.files.models import FileObject


def _file(db: Session, ctx: JobContext) -> FileObject:
    row = db.scalar(
        select(FileObject)
        .where(FileObject.tenant_id == ctx.tenant_id, FileObject.id == UUID(ctx.payload["file_id"]))
        .with_for_update()
    )
    if row is None:  # also the outcome for another tenant's file id (RLS + explicit filter)
        raise PermanentError("file not found in this workspace")
    return row


def _scan_dead(db: Session, ctx: JobContext) -> None:
    row = _file(db, ctx)
    if row.scan_status in ("pending", "error"):
        before = service.scan_outcome(db, row, "error", "scan could not be completed", None)
        audit.record(
            db,
            tenant_id=row.tenant_id,
            actor=Actor("system"),
            action=Action.FILE_SCAN_ERROR,
            entity_type="file",
            entity_id=row.id,
            before=before,
            after={"scan_status": "error"},
            meta={"request_id": f"job:{ctx.id}"},
        )


@job("files_scan", tenant_scoped=True, max_attempts=5, on_dead=_scan_dead)
def scan_file(db: Session, ctx: JobContext) -> None:
    row = _file(db, ctx)
    if row.scan_status in ("clean", "infected"):
        return  # already decided (idempotent re-run)
    if ctx.tenant_id is None:  # guaranteed by the runner for tenant-scoped jobs
        raise PermanentError("tenant-scoped job without tenant context")
    result = scanner.scan(ctx.settings, storage.read_chunks(ctx.settings, row.storage_key))
    meta = {"request_id": f"job:{ctx.id}"}
    if result.clean:
        target = storage.clean_key(ctx.tenant_id, row.kind, row.id, clock.now())
        if row.storage_key != target:
            storage.copy(ctx.settings, row.storage_key, target)
        quarantine = row.storage_key
        before = service.scan_outcome(db, row, "clean", None, target)
        audit.record(
            db,
            tenant_id=row.tenant_id,
            actor=Actor("system"),
            action=Action.FILE_SCANNED_CLEAN,
            entity_type="file",
            entity_id=row.id,
            before=before,
            after={"scan_status": "clean"},
            meta=meta,
        )
        db.flush()
        if quarantine != target:
            storage.delete(ctx.settings, quarantine)  # versioning keeps the history
        return
    before = service.scan_outcome(db, row, "infected", result.signature, None)  # stays in quarantine
    audit.record(
        db,
        tenant_id=row.tenant_id,
        actor=Actor("system"),
        action=Action.FILE_INFECTED,
        entity_type="file",
        entity_id=row.id,
        before=before,
        after={"scan_status": "infected"},
        meta=meta,
        metadata={"signature": result.signature},
    )
    jobs.enqueue(
        db,
        "files_notify_infected",
        tenant_id=row.tenant_id,
        payload={"file_id": str(row.id)},
        dedupe_key=f"files_notify_infected:{row.id}",
    )


@job("files_notify_infected", tenant_scoped=True)
def notify_infected(db: Session, ctx: JobContext) -> None:
    row = _file(db, ctx)
    if ctx.tenant_id is None:
        raise PermanentError("tenant-scoped job without tenant context")
    for email in identity.admin_emails(db, ctx.tenant_id):
        deliver(
            ctx.settings,
            Mail(
                template="file_infected",
                to=email,
                subject="Veyra Recover blocked an infected upload",
                body=(
                    "A file uploaded to your workspace was flagged by the malware scanner and blocked. "
                    "It cannot be downloaded or processed.\n\n"
                    f"File: {row.original_name}\nScanner result: {row.scan_detail}\n\n"
                    f"Review uploads at {ctx.settings.public_origin}/files"
                ),
            ),
        )
