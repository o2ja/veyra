"""files jobs: malware scan with quarantine -> clean promotion; admins emailed when a file is blocked.

``files_scan`` is idempotent: a file that already has a final outcome is left alone. A clamd outage
is transient (retried with backoff); when retries run out, ``on_dead`` marks the file ``error`` (never
usable) and emails the admins, who can retry the job from the jobs screen. The quarantine object is
deleted by a follow-up job, i.e. only after the promotion has committed.
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


def _enqueue_for(db: Session, kind: str, row: FileObject, key: str) -> None:
    jobs.enqueue(db, kind, tenant_id=row.tenant_id, payload={"file_id": str(row.id)}, dedupe_key=key)


def _notify_admins(db: Session, row: FileObject) -> None:
    _enqueue_for(db, "files_notify_blocked", row, f"files_notify_blocked:{row.id}:{row.scan_status}")


def _scan_dead(db: Session, ctx: JobContext) -> None:
    row = _file(db, ctx)
    if row.scan_status != "pending":
        return  # an earlier dead letter already marked it error (and notified)
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
    _notify_admins(db, row)


def _superseding_duplicate(db: Session, row: FileObject) -> FileObject | None:
    """A failed-scan file whose content was uploaded again meanwhile: the newer row owns the content
    (unique per tenant, kind and SHA-256 among non-error rows), so this one stays ``error``."""
    if row.scan_status != "error":
        return None
    return db.scalar(
        select(FileObject).where(
            FileObject.tenant_id == row.tenant_id,
            FileObject.kind == row.kind,
            FileObject.sha256 == row.sha256,
            FileObject.scan_status != "error",
            FileObject.id != row.id,
        )
    )


@job("files_scan", tenant_scoped=True, max_attempts=5, on_dead=_scan_dead)
def scan_file(db: Session, ctx: JobContext) -> None:
    row = _file(db, ctx)
    if row.scan_status in ("clean", "infected"):
        return  # already decided (idempotent re-run)
    if ctx.tenant_id is None:  # guaranteed by the runner for tenant-scoped jobs
        raise PermanentError("tenant-scoped job without tenant context")
    if (dup := _superseding_duplicate(db, row)) is not None:
        row.scan_detail = f"superseded by re-upload {dup.id}"
        return
    result = scanner.scan(ctx.settings, storage.read_chunks(ctx.settings, row.storage_key))
    meta = {"request_id": f"job:{ctx.id}"}
    if result.clean:
        target = storage.clean_key(ctx.tenant_id, row.kind, row.id, clock.now())
        storage.copy(ctx.settings, row.storage_key, target)
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
        _enqueue_for(db, "files_purge_quarantine", row, f"files_purge_quarantine:{row.id}")
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
    _notify_admins(db, row)


def _file_or_none(db: Session, ctx: JobContext) -> FileObject | None:
    """``None`` when the row is gone: an unused file deleted (``DELETE /files/{id}``) before this job ran."""
    try:
        return _file(db, ctx)
    except PermanentError:
        return None


@job("files_purge_quarantine", tenant_scoped=True)
def purge_quarantine(db: Session, ctx: JobContext) -> None:
    """Delete the quarantine copy of a promoted file (versioning keeps the history)."""
    row = _file_or_none(db, ctx)
    if row is None:
        return  # deleted meanwhile; files_delete_objects removes the quarantine copy too
    if row.scan_status != "clean" or ctx.tenant_id is None:
        raise PermanentError("only a promoted file's quarantine copy is purged")
    storage.delete(ctx.settings, storage.quarantine_key(ctx.tenant_id, row.id))


@job("files_notify_blocked", tenant_scoped=True)
def notify_blocked(db: Session, ctx: JobContext) -> None:
    """Email the workspace admins about an infected file or a scan that could not be completed."""
    row = _file_or_none(db, ctx)
    if row is None:
        return  # deleted meanwhile: nothing left to warn about
    if ctx.tenant_id is None:
        raise PermanentError("tenant-scoped job without tenant context")
    if row.scan_status == "infected":
        template, subject, what = (
            "file_infected",
            "Veyra Recover blocked an infected upload",
            "was flagged by the malware scanner and blocked",
        )
    elif row.scan_status == "error":
        template, subject, what = (
            "file_scan_failed",
            "Veyra Recover could not scan an upload",
            "could not be scanned for malware, so it is blocked (an admin can retry the scan on the Jobs page)",
        )
    else:
        return  # resolved meanwhile (an admin retry made it clean)
    for email in identity.admin_emails(db, ctx.tenant_id):
        deliver(
            ctx.settings,
            Mail(
                template=template,
                to=email,
                subject=subject,
                body=(
                    f"A file uploaded to your workspace {what}. It cannot be downloaded or processed.\n\n"
                    f"File: {row.original_name}\nScanner result: {row.scan_detail}\n\n"
                    f"Review uploads at {ctx.settings.public_origin}/files"
                ),
            ),
        )


@job("files_delete_objects", tenant_scoped=True)
def delete_objects(db: Session, ctx: JobContext) -> None:
    """Remove the stored object and any quarantine copy of a deleted file, after the row's deletion committed.
    Refuses a key outside the workspace or a file whose row still exists; a missing key is a no-op (idempotent)."""
    if ctx.tenant_id is None:
        raise PermanentError("tenant-scoped job without tenant context")
    if _file_or_none(db, ctx) is not None:
        raise PermanentError("the file still exists")
    file_id, key = UUID(ctx.payload["file_id"]), ctx.payload["key"]
    if not (key.startswith(f"tenants/{ctx.tenant_id}/") and key.endswith(f"/{file_id}")):
        key = storage.quarantine_key(
            ctx.tenant_id, file_id
        )  # never promoted, or a key that is not this file's
    for k in dict.fromkeys((key, storage.quarantine_key(ctx.tenant_id, file_id))):
        storage.delete(ctx.settings, k)
