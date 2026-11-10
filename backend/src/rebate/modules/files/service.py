"""files services: upload to quarantine, scan, promotion, presigned downloads (FR-FI-01..04).

Upload order: validate -> stream to the quarantine key -> insert ``file_objects`` (pending) + enqueue
``files_scan`` + audit in the request transaction. If the transaction fails after the object was
stored, the object is deleted again (best effort; a leftover quarantine object is never readable,
because only clean rows get download URLs).
"""

import hashlib
from dataclasses import dataclass
from typing import IO, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock, jobs
from rebate.core.errors import Conflict, InvalidState, NotFound
from rebate.core.ids import new_id
from rebate.core.request import Principal, RequestContext
from rebate.modules import audit
from rebate.modules.audit import Action, Actor
from rebate.modules.files import storage, validation
from rebate.modules.files.models import FileObject

DUPLICATE_KINDS = frozenset({"import", "remittance", "agreement"})
HEAD_BYTES = 8192


class DuplicateFile(Conflict):
    code = "duplicate_file"
    message = "This file was already uploaded."


@dataclass(frozen=True)
class Upload:
    kind: str
    filename: str
    declared_mime: str | None
    body: IO[bytes]  # seekable spooled file from the multipart parser


def _measure(body: IO[bytes], limit: int) -> tuple[str, int, bytes]:
    """SHA-256, size and the first bytes, streaming; stops as soon as the limit is passed."""
    digest, size, head = hashlib.sha256(), 0, b""
    body.seek(0)
    while chunk := body.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise validation.FileTooLarge()
        if len(head) < HEAD_BYTES:
            head += chunk[: HEAD_BYTES - len(head)]
        digest.update(chunk)
    body.seek(0)
    if size == 0:
        raise validation.UploadRejected(
            details=[{"field": "file", "code": "empty", "message": "The file is empty."}]
        )
    return digest.hexdigest(), size, head


def _existing(db: Session, tenant_id: UUID, kind: str, sha256: str) -> FileObject | None:
    if kind not in DUPLICATE_KINDS:
        return None
    return db.scalar(
        select(FileObject).where(
            FileObject.tenant_id == tenant_id,
            FileObject.kind == kind,
            FileObject.sha256 == sha256,
            FileObject.scan_status != "error",
        )
    )


def _duplicate(existing: FileObject) -> DuplicateFile:
    return DuplicateFile(details=[{"field": "file", "code": "duplicate", "message": str(existing.id)}])


def upload(c: RequestContext, principal: Principal, tenant_id: UUID, up: Upload) -> FileObject:
    name = validation.sanitise_filename(up.filename)
    ext = validation.check_name(up.kind, name, up.declared_mime)
    sha256, size, head = _measure(up.body, validation.max_bytes(c.settings, ext))
    checked = validation.check_content(ext, head, up.body)
    existing = _existing(c.db, tenant_id, up.kind, sha256)
    if existing is not None:
        raise _duplicate(existing)

    file_id = new_id()
    key = storage.quarantine_key(tenant_id, file_id)
    storage.put(c.settings, key, up.body)
    try:
        row = FileObject(
            tenant_id=tenant_id,
            id=file_id,
            kind=up.kind,
            original_name=name,
            storage_key=key,
            size_bytes=size,
            sha256=sha256,
            declared_mime=(up.declared_mime or "")[:200] or None,
            detected_mime=checked.detected_mime,
            uploaded_by=principal.user_id,
        )
        with c.db.begin_nested():
            c.db.add(row)
            c.db.flush()
        jobs.enqueue(
            c.db,
            "files_scan",
            tenant_id=tenant_id,
            payload={"file_id": str(file_id)},
            created_by=principal.user_id,
            dedupe_key=f"files_scan:{file_id}",
        )
        audit.record(
            c.db,
            tenant_id=tenant_id,
            actor=Actor("user", principal.user_id),
            action=Action.FILE_UPLOADED,
            entity_type="file",
            entity_id=file_id,
            after={"kind": up.kind, "original_name": name, "size_bytes": size, "sha256": sha256},
            meta=c.audit_meta(),
        )
    except IntegrityError:
        storage.delete(c.settings, key)
        raced = _existing(c.db, tenant_id, up.kind, sha256)  # concurrent identical upload won the race
        if raced is not None:
            raise _duplicate(raced) from None
        raise
    except Exception:
        storage.delete(c.settings, key)
        raise
    return row


def get(db: Session, tenant_id: UUID, file_id: UUID) -> FileObject:
    row = db.scalar(select(FileObject).where(FileObject.tenant_id == tenant_id, FileObject.id == file_id))
    if row is None:
        raise NotFound()
    return row


def list_files(db: Session, tenant_id: UUID, limit: int, before: UUID | None) -> list[FileObject]:
    stmt = select(FileObject).where(FileObject.tenant_id == tenant_id)
    if before is not None:
        stmt = stmt.where(FileObject.id < before)
    return list(db.scalars(stmt.order_by(FileObject.id.desc()).limit(limit)))


def download_url(c: RequestContext, principal: Principal, tenant_id: UUID, file_id: UUID) -> str:
    row = get(c.db, tenant_id, file_id)
    if row.scan_status != "clean":
        raise InvalidState("Only files that passed the malware scan can be downloaded.")
    ext = row.original_name.rsplit(".", 1)[-1].lower()
    url = storage.presigned_download(
        c.settings,
        row.storage_key,
        row.original_name,
        validation.CONTENT_TYPE.get(ext, "application/octet-stream"),
    )
    audit.record(
        c.db,
        tenant_id=tenant_id,
        actor=Actor("user", principal.user_id),
        action=Action.FILE_DOWNLOAD_URL_ISSUED,
        entity_type="file",
        entity_id=row.id,
        meta=c.audit_meta(),
        metadata={"expires_in_seconds": c.settings.presigned_url_seconds},
    )
    return url


def scan_outcome(
    db: Session, row: FileObject, status: str, detail: str | None, key: str | None
) -> dict[str, Any]:
    before = {"scan_status": row.scan_status}
    row.scan_status, row.scan_detail, row.scanned_at = status, detail, clock.now()
    if key is not None:
        row.storage_key = key
    db.flush()
    return before
