"""S10 jobs.

* ``refresh_summaries`` (tenant-scoped; W14): rebuilds the tenant's ``summary_claims_monthly`` in one transaction with
  set-based SQL (a handful of GROUP BY queries, no row loop, so a single attempt stays inside the lease), then schedules
  the next hourly refresh (deduped per hour, so any number of triggers leaves one chain; a dead-lettered run
  reschedules too). Triggered by a remittance finalisation, by ``POST /dashboard/refresh`` and by that chain.
* ``audit_pack`` (tenant-scoped; FR-RP-03): the trace export of one claim batch as CSV/XLSX: invoice line, source file
  row, agreement version, claim, confirmed payment. The file is stored like every other export (``file_objects`` kind
  ``export``); re-running after a crash is a no-op once the row exists."""

import hashlib
import io
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import NotFound
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError, enqueue, find_active, job
from rebate.modules import audit, claims, files, reconciliation
from rebate.modules.masterdata import Who
from rebate.modules.reporting import service

REFRESH_JOB = "refresh_summaries"
PACK_JOB = "audit_pack"
CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
HEADERS = (
    "Batch", "Claim reference", "Line type", "Invoice", "Invoice line", "Invoice date", "SKU", "Source file",
    "Source row", "Agreement", "Agreement version", "Quantity", "Unit amount", "Claim amount", "Paid (confirmed)",
    "Remittance documents",
)  # fmt: skip


def request_refresh(db: Session, tenant: UUID) -> UUID | None:
    """Queue a refresh now (one per minute and tenant; a second request in the same minute is the same job)."""
    now = clock.now()
    return enqueue(
        db, REFRESH_JOB, tenant_id=tenant, payload={}, dedupe_key=f"{REFRESH_JOB}:{tenant}:{now:%Y%m%d%H%M}"
    )


def _next_hour(db: Session, tenant: UUID) -> None:
    nxt = (clock.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    enqueue(
        db,
        REFRESH_JOB,
        tenant_id=tenant,
        payload={},
        run_at=nxt,
        dedupe_key=f"{REFRESH_JOB}:{tenant}:{nxt:%Y%m%d%H}",
    )


def _tenant(ctx: JobContext) -> UUID:
    if ctx.tenant_id is None:  # unreachable: the queue refuses a tenant-scoped job without a tenant
        raise PermanentError("tenant-scoped job without tenant context")
    return ctx.tenant_id


@job(REFRESH_JOB, tenant_scoped=True, on_dead=lambda db, ctx: _next_hour(db, _tenant(ctx)))
def refresh_summaries(db: Session, ctx: JobContext) -> None:
    tenant = _tenant(ctx)
    service.refresh(db, tenant)
    _next_hour(db, tenant)


def request_audit_pack(db: Session, who: Who, batch_id: UUID, fmt: str) -> tuple[UUID, UUID]:
    """Queue the pack of an existing batch and audit the request; returns ``(file_id, job_id)``."""
    tenant = who.tenant_id
    if claims.audit_pack_rows(db, tenant, batch_id) is None:
        raise NotFound()
    want = {"batch_id": str(batch_id), "format": fmt}
    active = find_active(db, PACK_JOB, tenant, want)  # same pack already in flight: same job
    if active is not None:
        return UUID(active.payload["file_id"]), active.id
    file_id = new_id()
    job_id = enqueue(
        db,
        PACK_JOB,
        tenant_id=tenant,
        payload={**want, "file_id": str(file_id)},
        created_by=who.user_id,
        dedupe_key=f"{PACK_JOB}:{file_id}",
    )
    if job_id is None:  # unreachable: the dedupe key embeds a fresh id
        raise RuntimeError("audit pack job was not enqueued")
    audit.record(
        db,
        tenant_id=tenant,
        actor=audit.Actor("user", who.user_id),
        action=audit.Action.AUDIT_PACK_REQUESTED,
        entity_type="claim_batch",
        entity_id=batch_id,
        after={"file_id": file_id, "format": fmt},
        meta=who.meta,
    )
    return file_id, job_id


@job(PACK_JOB, tenant_scoped=True, max_attempts=3)
def audit_pack(db: Session, ctx: JobContext) -> None:
    tenant = _tenant(ctx)
    if ctx.created_by is None:  # unreachable: enqueued by a user
        raise PermanentError("audit pack jobs need a user")
    try:
        file_id, batch_id, fmt = (
            UUID(ctx.payload["file_id"]),
            UUID(ctx.payload["batch_id"]),
            ctx.payload["format"],
        )
    except KeyError, ValueError:
        raise PermanentError("audit pack job without valid ids") from None
    if fmt not in CONTENT_TYPES:
        raise PermanentError("unknown audit pack format")
    if db.scalar(
        select(files.FileObject.id).where(
            files.FileObject.tenant_id == tenant, files.FileObject.id == file_id
        )
    ):
        return  # an earlier attempt finished
    rows = claims.audit_pack_rows(db, tenant, batch_id)
    if rows is None:
        raise PermanentError("the batch is gone")
    paid = reconciliation.confirmed_payments(
        db,
        tenant,
        [r["id"] for r in rows if r["kind"] == "claim_line"],
        [r["id"] for r in rows if r["kind"] == "adjustment"],
    )
    table = []
    for r in rows:
        amount, documents = paid.get(r["id"], (Decimal(0), None))
        table.append(
            (
                r["batch_number"], r["claim_reference"], r["line_type"], r.get("invoice_number"), r.get("invoice_line"),
                r.get("invoice_date"), r.get("sku"), r.get("source_file"), r.get("source_row"), r.get("agreement_number"),
                r.get("version_number"), r.get("quantity"), r.get("unit_amount"), r["claim_amount"], amount, documents,
            )
        )  # fmt: skip
    data = claims.render_table(fmt, "Audit pack", HEADERS, table)
    now = clock.now()
    key = files.storage.clean_key(tenant, "export", file_id, now)
    files.storage.put(ctx.settings, key, io.BytesIO(data))
    db.add(
        files.FileObject(
            tenant_id=tenant,
            id=file_id,
            kind="export",
            original_name=f"audit-pack-{rows[0]['batch_number'] if rows else batch_id}.{fmt}",
            storage_key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            detected_mime=CONTENT_TYPES[fmt],
            scan_status="clean",
            scan_detail="produced by the system",
            uploaded_by=ctx.created_by,
            scanned_at=now,
        )
    )
    db.flush()
    audit.record(
        db,
        tenant_id=tenant,
        actor=audit.Actor("user", ctx.created_by),
        action=audit.Action.AUDIT_PACK_EXPORTED,
        entity_type="claim_batch",
        entity_id=batch_id,
        after={"file_id": file_id, "format": fmt, "rows": len(rows)},
        meta={"request_id": f"job:{ctx.id}"},
    )
