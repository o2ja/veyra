"""Export job: a claim batch file (template columns) or the missed-claims report (CSV/XLSX).

The request allocates the file id up front (``result_url`` = ``/api/v1/files/{id}``, 404 until the job has run);
the job renders, stores the object under its final key, inserts the ``file_objects`` row (kind ``export``, written by
the system so ``clean`` without a scan) and audits. Re-running after a crash is a no-op once the row exists.
A batch export is linked on the batch (``exported_file_id``) while the batch is still ``approved``; a submitted
batch is immutable, so exporting it again only produces the file.
"""

import hashlib
import io
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError, enqueue, job
from rebate.modules import audit, files
from rebate.modules.claims import exporting, missed
from rebate.modules.claims.models import ClaimBatch, ClaimExportTemplate

JOB_KIND = "claims_export"
MAX_ROWS = 100_000

_FROM = (
    "FROM calculation_results r "
    "JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id "
    "LEFT JOIN products p ON p.tenant_id = s.tenant_id AND p.id = s.product_id "
    "LEFT JOIN customers c ON c.tenant_id = s.tenant_id AND c.id = s.customer_id "
    "LEFT JOIN agreements a ON a.tenant_id = r.tenant_id AND a.id = r.agreement_id "
    "LEFT JOIN manufacturers m ON m.tenant_id = r.tenant_id AND m.id = r.manufacturer_id "
)
_COMMON = (
    "m.code AS manufacturer_code, m.name AS manufacturer_name, a.external_number AS agreement_number, "
    "r.document_number, r.line_number, r.invoice_date, s.ship_date, s.sku_raw AS sku, "
    "p.description AS product_description, p.manufacturer_part_number, c.code AS customer_code, "
    "c.name AS customer_name, s.spa_reference, s.project_reference, r.claim_uom, s.unit_price, "
    "s.extended_amount, s.currency_code"
)


def enqueue_export(db: Session, tenant_id: UUID, user_id: UUID, payload: dict[str, str]) -> tuple[UUID, UUID]:
    """Queue an export; returns ``(file_id, job_id)``."""
    file_id = new_id()
    job_id = enqueue(
        db,
        JOB_KIND,
        tenant_id=tenant_id,
        payload={**payload, "file_id": str(file_id)},
        created_by=user_id,
        dedupe_key=f"{JOB_KIND}:{file_id}",
    )
    if job_id is None:  # unreachable: the dedupe key embeds a fresh id
        raise RuntimeError("export job was not enqueued")
    return file_id, job_id


def batch_rows(db: Session, tenant_id: UUID, batch_id: UUID) -> list[dict[str, Any]]:
    """The batch as the manufacturer receives it: the sales-line claims, then the rebate claims, then the batch's
    adjustments (signed amounts, against the claim line they correct), so the file ties out to the batch total."""
    params = {"t": tenant_id, "b": batch_id}
    claims = db.execute(
        text(
            "SELECT cl.claim_reference, b.batch_number, cl.quantity, cl.unit_amount, cl.claim_amount, "
            f"'claim' AS line_type, {_COMMON} {_FROM} "
            "JOIN claim_lines cl ON cl.tenant_id = r.tenant_id AND cl.calculation_result_id = r.id "
            "JOIN claim_batches b ON b.tenant_id = cl.tenant_id AND b.id = cl.batch_id "
            "WHERE cl.tenant_id = :t AND cl.batch_id = :b ORDER BY cl.claim_reference"
        ),
        params,
    )
    rebates = db.execute(
        text(
            "SELECT cl.claim_reference, b.batch_number, cl.claim_amount, 'rebate_claim' AS line_type, "
            "m.code AS manufacturer_code, m.name AS manufacturer_name, ag.external_number AS agreement_number "
            "FROM claim_lines cl JOIN claim_batches b ON b.tenant_id = cl.tenant_id AND b.id = cl.batch_id "
            "JOIN rebate_accrual_entries e ON e.tenant_id = cl.tenant_id AND e.id = cl.rebate_accrual_entry_id "
            "JOIN agreements ag ON ag.tenant_id = e.tenant_id AND ag.id = e.agreement_id "
            "JOIN manufacturers m ON m.tenant_id = e.tenant_id AND m.id = e.manufacturer_id "
            "WHERE cl.tenant_id = :t AND cl.batch_id = :b AND cl.source_kind = 'rebate_accrual' "
            "ORDER BY cl.claim_reference"
        ),
        params,
    )
    adjusted = db.execute(
        text(
            "SELECT cl.claim_reference, b.batch_number, r.eligible_quantity AS quantity, "
            "r.claim_unit_amount AS unit_amount, adj.amount AS claim_amount, "
            "'adjustment:' || adj.adjustment_type AS line_type, m.code AS manufacturer_code, "
            "m.name AS manufacturer_name, ag.external_number AS agreement_number, r.document_number, r.line_number, "
            "r.invoice_date, s.ship_date, s.sku_raw AS sku, p.description AS product_description, "
            "p.manufacturer_part_number, c.code AS customer_code, c.name AS customer_name, s.spa_reference, "
            "s.project_reference, r.claim_uom, s.unit_price, s.extended_amount, s.currency_code "
            "FROM claim_adjustments adj JOIN claim_batches b ON b.tenant_id = adj.tenant_id "
            "AND b.id = adj.included_in_batch_id "
            "LEFT JOIN claim_lines cl ON cl.tenant_id = adj.tenant_id AND cl.id = adj.claim_line_id "
            "LEFT JOIN calculation_results r ON r.tenant_id = adj.tenant_id AND r.id = adj.calculation_result_id "
            "LEFT JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id "
            "LEFT JOIN products p ON p.tenant_id = s.tenant_id AND p.id = s.product_id "
            "LEFT JOIN customers c ON c.tenant_id = s.tenant_id AND c.id = s.customer_id "
            "LEFT JOIN rebate_accrual_entries e ON e.tenant_id = adj.tenant_id AND e.id = adj.rebate_accrual_entry_id "
            "LEFT JOIN agreements ag ON ag.tenant_id = adj.tenant_id AND ag.id = COALESCE(r.agreement_id, e.agreement_id) "
            "LEFT JOIN manufacturers m ON m.tenant_id = adj.tenant_id AND m.id = adj.manufacturer_id "
            "WHERE adj.tenant_id = :t AND adj.included_in_batch_id = :b ORDER BY adj.id"
        ),
        params,
    )
    blank = dict.fromkeys(exporting.FIELDS)
    return [blank | dict(r._mapping) for result in (claims, rebates, adjusted) for r in result]


def missed_rows(db: Session, tenant_id: UUID, ids: list[UUID]) -> list[dict[str, Any]]:
    sql = (
        "SELECT NULL::text AS claim_reference, NULL::text AS batch_number, r.eligible_quantity AS quantity, "
        f"r.claim_unit_amount AS unit_amount, r.claim_amount, {_COMMON} {_FROM} "
        "WHERE r.tenant_id = :t AND r.id = ANY(:ids) ORDER BY r.invoice_date, r.document_number, r.line_number"
    )
    return [dict(r._mapping) for r in db.execute(text(sql), {"t": tenant_id, "ids": ids})]


def _store(
    db: Session, ctx: JobContext, tenant_id: UUID, file_id: UUID, name: str, fmt: str, data: bytes
) -> None:

    now = clock.now()
    key = files.storage.clean_key(tenant_id, "export", file_id, now)
    files.storage.put(ctx.settings, key, io.BytesIO(data))
    db.add(
        files.FileObject(
            tenant_id=tenant_id,
            id=file_id,
            kind="export",
            original_name=name,
            storage_key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            detected_mime=exporting.CONTENT_TYPES[fmt],
            scan_status="clean",
            scan_detail="produced by the system",
            uploaded_by=ctx.created_by,
            scanned_at=now,
        )
    )
    db.flush()


@job(JOB_KIND, tenant_scoped=True, max_attempts=3)
def claims_export(db: Session, ctx: JobContext) -> None:
    if ctx.tenant_id is None or ctx.created_by is None:  # unreachable: tenant-scoped, enqueued by a user
        raise PermanentError("export jobs need a tenant and a user")
    try:
        file_id, kind = UUID(ctx.payload["file_id"]), ctx.payload["kind"]
    except KeyError, ValueError:
        raise PermanentError("export job without a valid file id") from None
    if db.scalar(
        select(files.FileObject.id).where(
            files.FileObject.tenant_id == ctx.tenant_id, files.FileObject.id == file_id
        )
    ):
        return  # an earlier attempt finished
    meta = {"request_id": f"job:{ctx.id}"}
    actor = audit.Actor("user", ctx.created_by)
    if kind == "batch":
        batch = db.scalar(
            select(ClaimBatch)
            .where(ClaimBatch.tenant_id == ctx.tenant_id, ClaimBatch.id == UUID(ctx.payload["batch_id"]))
            .with_for_update()
        )
        if batch is None or batch.status not in ("approved", "submitted"):
            raise PermanentError("the batch is gone or no longer exportable")
        template = None
        if "template_id" in ctx.payload:
            template = db.scalar(
                select(ClaimExportTemplate).where(
                    ClaimExportTemplate.tenant_id == ctx.tenant_id,
                    ClaimExportTemplate.id == UUID(ctx.payload["template_id"]),
                )
            )
            if template is None:
                raise PermanentError("the export template is gone")
        fmt = template.format if template else "csv"
        rows = batch_rows(db, ctx.tenant_id, batch.id)
        try:
            data = exporting.render(
                fmt,
                template.columns if template else exporting.DEFAULT_COLUMNS,
                template.options if template else {},
                rows,
            )
        except exporting.FormatWouldRound as exc:
            raise PermanentError(f"the template would round a frozen value: {exc}") from None
        _store(db, ctx, ctx.tenant_id, file_id, f"{batch.batch_number}.{fmt}", fmt, data)
        after: dict[str, Any] = {
            "exported_file_id": file_id,
            "export_template_id": template.id if template else None,
        }
        if batch.status == "approved":
            batch.export_template_id = template.id if template else None
            batch.exported_file_id = file_id
            db.flush()
        audit.record(
            db,
            tenant_id=ctx.tenant_id,
            actor=actor,
            action=audit.Action.CLAIM_BATCH_EXPORTED,
            entity_type="claim_batch",
            entity_id=batch.id,
            after=after,
            meta=meta,
        )
        return
    fmt = ctx.payload["format"]
    filt = {
        k: ctx.payload[k]
        for k in ("manufacturer_id", "agreement_id", "invoice_from", "invoice_to")
        if k in ctx.payload
    }
    found = missed.missed(
        db,
        ctx.tenant_id,
        manufacturer_id=UUID(filt["manufacturer_id"]) if "manufacturer_id" in filt else None,
        agreement_id=UUID(filt["agreement_id"]) if "agreement_id" in filt else None,
        invoice_from=date.fromisoformat(filt["invoice_from"]) if "invoice_from" in filt else None,
        invoice_to=date.fromisoformat(filt["invoice_to"]) if "invoice_to" in filt else None,
        limit=MAX_ROWS + 1,
    )
    if len(found) > MAX_ROWS:
        raise PermanentError(f"more than {MAX_ROWS} rows: narrow the filter")
    data = exporting.render(
        fmt, exporting.MISSED_COLUMNS, {}, missed_rows(db, ctx.tenant_id, [r.id for r in found])
    )
    _store(db, ctx, ctx.tenant_id, file_id, f"missed-claims-{clock.now():%Y-%m-%d}.{fmt}", fmt, data)
    audit.record(
        db,
        tenant_id=ctx.tenant_id,
        actor=actor,
        action=audit.Action.MISSED_CLAIMS_EXPORTED,
        entity_type="missed_claims_export",
        entity_id=file_id,
        after={"format": fmt, "filter": {**filt, "rows": len(found)}},
        meta=meta,
    )
