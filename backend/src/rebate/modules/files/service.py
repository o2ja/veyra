"""files services: upload to quarantine, scan, promotion, presigned downloads (FR-FI-01..04).

Upload order: validate -> stream to the quarantine key -> insert ``file_objects`` (pending) + enqueue
``files_scan`` + audit in the request transaction. If the transaction fails after the object was
stored, the object is deleted again (best effort; a leftover quarantine object is never readable,
because only clean rows get download URLs).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any, Literal
from uuid import UUID

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock, jobs
from rebate.core.access import PermissionDenied
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


# Export files carry no column saying which export made them, so the producer's name pattern decides:
# audit pack "audit-pack-*" -> audit_pack.export, missed-claims list "missed-claims-*" -> data.read,
# anything else (claim batch exports, named by batch number) -> claim_batch.export.
_EXPORT_PREFIXES = (("audit-pack-", "audit_pack.export"), ("missed-claims-", "data.read"))


def export_permission(row: FileObject) -> str | None:
    """The permission needed to read ``row``, beyond ``data.read``; None for non-export files."""
    if row.kind != "export":
        return None
    return next(
        (p for prefix, p in _EXPORT_PREFIXES if row.original_name.startswith(prefix)), "claim_batch.export"
    )


def check_readable(row: FileObject, principal: Principal) -> None:
    needed = export_permission(row)
    if needed is not None and needed not in principal.permissions:
        raise PermissionDenied(needed)


def get(db: Session, tenant_id: UUID, file_id: UUID) -> FileObject:
    row = db.scalar(select(FileObject).where(FileObject.tenant_id == tenant_id, FileObject.id == file_id))
    if row is None:
        raise NotFound()
    return row


@dataclass(frozen=True)
class Use:
    """A record that points at a file (``GET /files/{id}`` ``used_by``)."""

    kind: str
    id: UUID
    label: str


UseSource = Callable[[Session, UUID, UUID], list[Use]]
_USE_SOURCES: list[UseSource] = []


def register_use_source(source: UseSource) -> UseSource:
    """Modules that reference ``file_objects`` register a reader of their references (files cannot import them)."""
    _USE_SOURCES.append(source)
    return source


def used_by(db: Session, tenant_id: UUID, file_id: UUID) -> list[Use]:
    return [u for source in _USE_SOURCES for u in source(db, tenant_id, file_id)]


def remove(
    c: RequestContext, principal: Principal, tenant_id: UUID, file_id: UUID
) -> Literal["deleted", "archived"]:
    """Delete an unused file (row now, stored object by a job after commit) or archive a used one. A foreign key
    refusal (a reference no module reported) also archives, so a file behind any record is never removed."""
    row = c.db.scalar(
        select(FileObject)
        .where(FileObject.tenant_id == tenant_id, FileObject.id == file_id)
        .with_for_update()
    )
    if row is None:
        raise NotFound()
    check_readable(row, principal)
    if row.archived_at is not None:
        return "archived"
    actor, meta = Actor("user", principal.user_id), c.audit_meta()
    if not used_by(c.db, tenant_id, file_id):
        if row.scan_status == "pending":
            raise InvalidState("Wait until the malware scan of this file has finished.")
        snapshot = {"kind": row.kind, "original_name": row.original_name, "sha256": row.sha256}
        key = row.storage_key
        try:
            with c.db.begin_nested():
                deleted = c.db.scalar(select(func.fn_delete_unreferenced_file(file_id)))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23503":
                raise
            deleted = False
        if deleted:
            c.db.expunge(row)
            jobs.enqueue(
                c.db,
                "files_delete_objects",
                tenant_id=tenant_id,
                payload={"file_id": str(file_id), "key": key},
                created_by=principal.user_id,
                dedupe_key=f"files_delete_objects:{file_id}",
            )
            audit.record(
                c.db, tenant_id=tenant_id, actor=actor, action=Action.FILE_DELETED, entity_type="file",
                entity_id=file_id, before=snapshot, meta=meta,
            )  # fmt: skip
            return "deleted"
    row.archived_at = clock.now()
    c.db.flush()
    audit.record(
        c.db, tenant_id=tenant_id, actor=actor, action=Action.FILE_ARCHIVED, entity_type="file",
        entity_id=file_id, after={"archived_at": row.archived_at}, meta=meta,
    )  # fmt: skip
    return "archived"


def list_files(
    db: Session,
    principal: Principal,
    tenant_id: UUID,
    limit: int,
    before: UUID | None,
    include_archived: bool = False,
) -> list[FileObject]:
    stmt = select(FileObject).where(FileObject.tenant_id == tenant_id)
    if not include_archived:
        stmt = stmt.where(FileObject.archived_at.is_(None))
    hidden = [  # export files whose permission the caller lacks (see export_permission)
        and_(FileObject.kind == "export", FileObject.original_name.startswith(prefix))
        for prefix, p in _EXPORT_PREFIXES
        if p not in principal.permissions
    ]
    if "claim_batch.export" not in principal.permissions:
        named = or_(*(FileObject.original_name.startswith(prefix) for prefix, _ in _EXPORT_PREFIXES))
        hidden.append(and_(FileObject.kind == "export", not_(named)))
    if hidden:
        stmt = stmt.where(not_(or_(*hidden)))
    if before is not None:
        stmt = stmt.where(FileObject.id < before)
    return list(db.scalars(stmt.order_by(FileObject.id.desc()).limit(limit)))


def download_url(c: RequestContext, principal: Principal, tenant_id: UUID, file_id: UUID) -> str:
    row = get(c.db, tenant_id, file_id)
    check_readable(row, principal)
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
