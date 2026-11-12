"""Remittance documents: request, the ingest job (file or PDF -> staging rows), human acceptance, void, reads
(BUSINESS_LOGIC.md §10.1, AI_PROCESSING.md §8, API_SPECIFICATION.md §2.7).

CSV/XLSX are read through the ingestion readers and a ``remittance`` mapping (saved template or header synonyms) and,
when every deterministic check passes, accepted for the requesting user. A PDF is read through the AI gateway
(recorded responses in tests), always lands in ``pending_review`` and is accepted by a person. Lines become matchable
(``remittance_lines``) only on acceptance, in one transaction with the arithmetic check (also enforced by a trigger)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, InvalidState, NotFound, ValidationFailed
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError, enqueue, job
from rebate.modules import audit, files, ingestion, masterdata, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.extraction import service as extraction
from rebate.modules.extraction.models import DocumentExtraction
from rebate.modules.ingestion import service as ingestion_service
from rebate.modules.ingestion.validators import MappingInvalid
from rebate.modules.masterdata import Who, keyset
from rebate.modules.masterdata import service as masterdata_service
from rebate.modules.reconciliation import intake, pdf
from rebate.modules.reconciliation.models import RemittanceDocument, RemittanceLine, RemittanceStagingLine

log = structlog.get_logger("rebate.reconciliation")

JOB = "remittance_ingest"
SCAN_WAIT = 600  # seconds a pending malware scan is awaited
FILE_TYPES = ("csv", "xlsx", "pdf")
OPEN = ("uploaded", "pending_review")
CHUNK = 2000
# staging/line column <- staged-row key
COLUMNS = {
    "line_number": "line_number",
    "source_row_number": "source_row_number",
    "claim_reference": "claim_reference",
    "invoice_number": "invoice_number",
    "invoice_line_number": "invoice_line_number",
    "sku_raw": "sku",
    "customer_ref": "customer_ref",
    "quantity": "quantity",
    "amount_paid": "amount_paid",
    "amount_claimed_stated": "amount_claimed_stated",
    "external_reason_code": "external_reason_code",
    "external_reason_text": "external_reason_text",
}
CORRECTABLE = (
    "claim_reference",
    "invoice_number",
    "invoice_line_number",
    "sku",
    "customer_ref",
    "quantity",
    "amount_paid",
    "amount_claimed_stated",
    "external_reason_code",
    "external_reason_text",
)


class DuplicateRemittance(Conflict):
    code = "duplicate_remittance"
    message = "A remittance with this document number already exists for this manufacturer."


class CannotAccept(Conflict):
    commit = True  # the user's corrections are kept; the refusal itself changes nothing
    code = "cannot_accept"
    message = "The remittance has open problems and cannot be accepted."


def _audit(
    db: Session,
    tenant_id: UUID,
    actor: Actor,
    meta: dict[str, Any],
    action: Action,
    doc: RemittanceDocument,
    **kw: Any,
) -> None:
    audit.record(
        db,
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        entity_type="remittance",
        entity_id=doc.id,
        meta=meta,
        **kw,
    )


def view(doc: RemittanceDocument) -> dict[str, Any]:
    """The audited facts of a document; money and dates as text (the audit log is JSON)."""
    out: dict[str, Any] = {
        "manufacturer_id": doc.manufacturer_id,
        "file_id": doc.file_id,
        "doc_type": doc.doc_type,
        "channel": doc.channel,
        "document_number": doc.document_number,
        "document_date": doc.document_date,
        "payment_date": doc.payment_date,
        "total_amount": doc.total_amount,
        "status": doc.status,
        "extraction_id": doc.extraction_id,
    }
    return {k: v if v is None or isinstance(v, str | int) else str(v) for k, v in out.items()}


# ---- reads --------------------------------------------------------------------------------------------------


def get(db: Session, tenant_id: UUID, remittance_id: UUID, *, lock: bool = False) -> RemittanceDocument:
    stmt = select(RemittanceDocument).where(
        RemittanceDocument.tenant_id == tenant_id, RemittanceDocument.id == remittance_id
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def list_documents(
    db: Session,
    tenant_id: UUID,
    *,
    status: str | None,
    manufacturer_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> list[RemittanceDocument]:
    stmt = select(RemittanceDocument).where(RemittanceDocument.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(RemittanceDocument.status == status)
    if manufacturer_id is not None:
        stmt = stmt.where(RemittanceDocument.manufacturer_id == manufacturer_id)
    return list(db.scalars(keyset(stmt, RemittanceDocument.id, cursor, limit)))


def list_lines(
    db: Session, tenant_id: UUID, remittance_id: UUID, *, limit: int, cursor: str | None
) -> list[RemittanceLine]:
    get(db, tenant_id, remittance_id)
    stmt = select(RemittanceLine).where(
        RemittanceLine.tenant_id == tenant_id, RemittanceLine.remittance_document_id == remittance_id
    )
    return list(db.scalars(keyset(stmt, RemittanceLine.id, cursor, limit, newest_first=False)))


def staged(db: Session, tenant_id: UUID, remittance_id: UUID) -> list[RemittanceStagingLine]:
    return list(
        db.scalars(
            select(RemittanceStagingLine)
            .where(
                RemittanceStagingLine.tenant_id == tenant_id,
                RemittanceStagingLine.remittance_document_id == remittance_id,
            )
            .order_by(RemittanceStagingLine.line_number)
        )
    )


def _as_row(s: RemittanceStagingLine) -> dict[str, Any]:
    return {"amount_paid": s.amount_paid, "issues": list(s.issues)}


def problems(doc: RemittanceDocument, rows: list[RemittanceStagingLine]) -> list[intake.Issue]:
    """What stops acceptance now: row issues plus the document arithmetic."""
    return intake.blocking([_as_row(s) for s in rows], doc.total_amount)


def extraction_view(db: Session, tenant_id: UUID, remittance_id: UUID) -> dict[str, Any]:
    doc = get(db, tenant_id, remittance_id)
    rows = staged(db, tenant_id, remittance_id)
    return {"remittance": doc, "lines": rows, "problems": problems(doc, rows) if doc.status in OPEN else []}


# ---- request ------------------------------------------------------------------------------------------------


def _duplicate(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, number: str | None, own: UUID | None
) -> bool:
    if number is None:
        return False
    found = db.scalar(
        select(RemittanceDocument.id).where(
            RemittanceDocument.tenant_id == tenant_id,
            RemittanceDocument.manufacturer_id == manufacturer_id,
            RemittanceDocument.document_number == number,
            RemittanceDocument.status != "void",
            RemittanceDocument.id != (own or UUID(int=0)),
        )
    )
    return found is not None


def _invalid(field: str, code: str, message: str) -> ValidationFailed:
    return ValidationFailed(details=[{"field": field, "code": code, "message": message}])


def create(db: Session, who: Who, body: Any) -> tuple[RemittanceDocument, UUID]:
    t = who.tenant_id
    try:
        file = files.get(db, t, body.file_id)
    except NotFound:
        raise _invalid("file_id", "not_found", "Unknown file.") from None
    kind = file.original_name.rsplit(".", 1)[-1].lower()
    if file.kind != "remittance" or kind not in FILE_TYPES:
        raise _invalid("file_id", "wrong_kind", "Upload a CSV, XLSX or PDF with kind 'remittance'.")
    if file.scan_status in ("infected", "error"):
        raise InvalidState("This file did not pass the malware scan and cannot be used.")
    if masterdata.existing_manufacturer_ids(db, t, [body.manufacturer_id]) != {body.manufacturer_id}:
        raise _invalid("manufacturer_id", "not_found", "Unknown manufacturer.")
    if body.mapping_template_id is not None:
        try:
            template = ingestion_service.get_template(db, t, body.mapping_template_id)
        except NotFound:
            raise _invalid("mapping_template_id", "not_found", "Unknown mapping template.") from None
        if template.import_type != "remittance":
            raise _invalid("mapping_template_id", "wrong_type", "The template is for another import type.")
    if db.scalar(
        select(RemittanceDocument.id).where(
            RemittanceDocument.tenant_id == t,
            RemittanceDocument.file_id == file.id,
            RemittanceDocument.status != "void",
        )
    ):
        raise Conflict("This file is already used by a remittance.")
    if _duplicate(db, t, body.manufacturer_id, body.document_number, None):
        raise DuplicateRemittance()
    extraction_id = None
    if kind == "pdf":
        if not tenancy.get_settings(db, t).ai_processing_enabled:
            raise extraction.AiNotEnabled(
                "AI processing is not enabled for this workspace; upload the remittance as CSV or XLSX."
            )
        extraction_id = new_id()
        db.add(
            DocumentExtraction(
                tenant_id=t,
                id=extraction_id,
                file_id=file.id,
                kind="remittance",
                status="queued",
                created_by=who.user_id,
            )
        )
        db.flush()
    doc = RemittanceDocument(
        tenant_id=t,
        id=new_id(),
        manufacturer_id=body.manufacturer_id,
        file_id=file.id,
        doc_type=body.doc_type,
        channel=body.channel,
        document_number=body.document_number,
        document_date=body.document_date,
        payment_date=body.payment_date,
        total_amount=body.total_amount if body.total_amount is not None else Decimal(0),
        currency_code=body.currency_code,
        status="uploaded",
        extraction_id=extraction_id,
        mapping_template_id=body.mapping_template_id,
        created_by=who.user_id,
        created_at=clock.now(),
    )
    db.add(doc)
    try:
        db.flush()
    except IntegrityError:
        raise DuplicateRemittance() from None
    job_id = enqueue(
        db,
        JOB,
        tenant_id=t,
        payload={"remittance_id": str(doc.id), "total_given": "1" if body.total_amount is not None else ""},
        created_by=who.user_id,
        dedupe_key=f"{JOB}:{doc.id}:0",
    )
    if job_id is None:  # the dedupe key embeds a fresh id
        raise RuntimeError("job was not enqueued")
    _audit(db, t, Actor("user", who.user_id), who.meta, Action.REMITTANCE_CREATED, doc, after=view(doc))
    return doc, job_id


# ---- the job ------------------------------------------------------------------------------------------------


def _void(
    db: Session, doc: RemittanceDocument, code: str, message: str, actor: Actor, meta: dict[str, Any]
) -> None:
    before = view(doc)
    doc.status = "void"
    db.flush()
    _audit(
        db, doc.tenant_id, actor, meta, Action.REMITTANCE_VOIDED, doc,
        before=before, after={"status": "void", "failure": f"{code}: {message}"[:300]},
    )  # fmt: skip
    log.info("remittance.void", remittance_id=str(doc.id), code=code)


def _store(db: Session, doc: RemittanceDocument, rows: list[dict[str, Any]]) -> None:
    db.execute(delete(RemittanceStagingLine).where(RemittanceStagingLine.remittance_document_id == doc.id))
    intake.number_lines(rows)
    for start in range(0, len(rows), CHUNK):
        db.execute(
            insert(RemittanceStagingLine),
            [
                {
                    "tenant_id": doc.tenant_id,
                    "id": new_id(),
                    "remittance_document_id": doc.id,
                    **{col: r.get(key) for col, key in COLUMNS.items()},
                    "raw": r.get("raw"),
                    "evidence": r.get("evidence"),
                    "issues": intake.row_issues(r),
                }
                for r in rows[start : start + CHUNK]
            ],
        )


def ingest(db: Session, ctx: JobContext) -> None:
    if ctx.tenant_id is None:
        raise PermanentError("tenant-scoped job without tenant context")
    doc = get(db, ctx.tenant_id, UUID(ctx.payload["remittance_id"]), lock=True)
    if doc.status != "uploaded":
        return  # a repeated job is a no-op
    file = files.get(db, doc.tenant_id, doc.file_id)
    system, meta = Actor("system"), {"request_id": "job"}
    if file.scan_status == "pending":
        if (clock.now() - doc.created_at).total_seconds() > SCAN_WAIT:
            _void(db, doc, "scan_timeout", "The malware scan did not finish in time.", system, meta)
        else:
            enqueue(
                db, JOB, tenant_id=doc.tenant_id, payload=ctx.payload,
                run_at=clock.now() + timedelta(seconds=5),
            )  # fmt: skip
        return
    if file.scan_status != "clean":
        _void(db, doc, "scan_failed", "The file did not pass the malware scan.", system, meta)
        return
    data = files.read_clean(ctx.settings, file)
    kind = file.original_name.rsplit(".", 1)[-1].lower()
    try:
        if kind == "pdf":
            if doc.extraction_id is None:  # set at request time for every PDF
                raise PermanentError("PDF remittance without an extraction")
            header, rows = pdf.extract_lines(ctx, doc.tenant_id, doc.extraction_id, data)
            if header.get("total_issue"):
                rows[0]["issues"].append(header["total_issue"])
        else:
            template = None
            if doc.mapping_template_id is not None:
                t = ingestion_service.get_template(db, doc.tenant_id, doc.mapping_template_id)
                template = {"mapping": t.mapping, "options": t.options}
            rows, _mapping, header = intake.read_table(data, kind, template)
    except pdf.Refused as exc:
        _void(db, doc, exc.code, exc.message, system, meta)
        return
    except (ingestion.ReadError, files.UndecodableText, MappingInvalid, intake.TooManyLines) as exc:
        code = getattr(
            exc, "code", "mapping_invalid" if isinstance(exc, MappingInvalid) else "too_many_lines"
        )
        _void(db, doc, code, str(getattr(exc, "message", exc))[:200], system, meta)
        return
    if not rows:
        _void(db, doc, "empty_file", "The file has no lines.", system, meta)
        return
    number = doc.document_number or header.get("document_number")
    if _duplicate(db, doc.tenant_id, doc.manufacturer_id, number, doc.id):
        _void(db, doc, "duplicate_remittance", f"Document number {number} already exists.", system, meta)
        return
    total = doc.total_amount
    if not ctx.payload.get("total_given"):
        stated = header.get("total_amount")
        total = (
            stated
            if stated is not None
            else sum((r.get("amount_paid") or Decimal(0) for r in rows), Decimal(0))
        )
    doc.document_number = number
    doc.document_date = doc.document_date or header.get("document_date")
    doc.payment_date = doc.payment_date or header.get("payment_date")
    doc.currency_code = doc.currency_code or header.get("currency_code")
    doc.total_amount = total
    _store(db, doc, rows)
    db.flush()
    if kind != "pdf" and not intake.blocking(rows, total):
        _accept(
            db, doc, doc.created_by, Actor("system"), meta, auto=True
        )  # deterministic file, all checks passed
        return
    doc.status = "pending_review"
    db.flush()
    _audit(
        db, doc.tenant_id, system, meta, Action.REMITTANCE_STAGED, doc,
        after={"status": "pending_review", "line_count": len(rows)},
    )  # fmt: skip


def on_dead(db: Session, ctx: JobContext) -> None:
    """A job that ran out of attempts voids its remittance (a visible end state)."""
    if ctx.tenant_id is None:
        return
    doc = get(db, ctx.tenant_id, UUID(ctx.payload["remittance_id"]), lock=True)
    if doc.status in OPEN:
        _void(
            db, doc, "job_failed", "The ingest job failed repeatedly.", Actor("system"), {"request_id": "job"}
        )


@job(JOB, tenant_scoped=True, max_attempts=3, on_dead=on_dead)
def remittance_ingest(db: Session, ctx: JobContext) -> None:
    ingest(db, ctx)


# ---- accept / void ------------------------------------------------------------------------------------------


def _reason_map(db: Session, tenant_id: UUID, manufacturer_id: UUID) -> dict[str, str]:
    return {
        m.external_code: m.reason_code
        for m in masterdata_service.list_reason_code_mappings(db, tenant_id, manufacturer_id)
    }


def _accept(
    db: Session, doc: RemittanceDocument, user_id: UUID, actor: Actor, meta: dict[str, Any], *, auto: bool
) -> None:
    rows = staged(db, doc.tenant_id, doc.id)
    if issues := problems(doc, rows):
        raise CannotAccept(details=issues[:50])
    mapping = _reason_map(db, doc.tenant_id, doc.manufacturer_id)
    lines = []
    for s in rows:
        ext = s.external_reason_code
        reason = None if not ext else mapping.get(ext, mapping.get(ext.upper(), "unknown"))
        lines.append(
            {
                "tenant_id": doc.tenant_id,
                "id": new_id(),
                "remittance_document_id": doc.id,
                "line_number": s.line_number,
                "source_row_number": s.source_row_number,
                "claim_reference": s.claim_reference,
                "invoice_number": s.invoice_number,
                "invoice_line_number": s.invoice_line_number,
                "sku_raw": s.sku_raw,
                "customer_ref": s.customer_ref,
                "quantity": s.quantity,
                "amount_paid": s.amount_paid,
                "amount_claimed_stated": s.amount_claimed_stated,
                "external_reason_code": ext,
                "external_reason_text": s.external_reason_text,
                "reason_code": reason,
                "raw": s.raw,
            }
        )
    for start in range(0, len(lines), CHUNK):
        db.execute(insert(RemittanceLine), lines[start : start + CHUNK])
    before = view(doc)
    doc.status, doc.accepted_by, doc.accepted_at = "accepted", user_id, clock.now()
    db.flush()  # the database re-checks: lines present and |sum - total| <= 0.01
    _audit(
        db, doc.tenant_id, actor, meta, Action.REMITTANCE_ACCEPTED, doc,
        before=before, after=view(doc) | {"line_count": len(lines), "run": "auto" if auto else "reviewed"},
    )  # fmt: skip


def _apply_corrections(db: Session, doc: RemittanceDocument, body: Any) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    header = {
        k: getattr(body, k) for k in ("document_number", "document_date", "payment_date", "total_amount")
    }
    if any(v is not None for v in header.values()):
        if _duplicate(db, doc.tenant_id, doc.manufacturer_id, header["document_number"], doc.id):
            raise DuplicateRemittance()
        for k, v in header.items():
            if v is not None:
                setattr(doc, k, v)
        changed.append({"header": sorted(k for k, v in header.items() if v is not None)})
        if (
            header["total_amount"] is not None
        ):  # the user vouches for the total: its "not in document" flag goes
            db.execute(
                text(
                    "UPDATE remittance_extraction_staging SET issues = (SELECT COALESCE(jsonb_agg(i), '[]') "
                    "FROM jsonb_array_elements(issues) i WHERE i->>'field' <> 'total_amount') "
                    "WHERE tenant_id = :t AND remittance_document_id = :d"
                ),
                {"t": doc.tenant_id, "d": doc.id},
            )
    by_number = {s.line_number: s for s in staged(db, doc.tenant_id, doc.id)}
    for c in body.corrections:
        s = by_number.get(c.line_number)
        if s is None:
            raise _invalid("corrections", "unknown_line", f"No line {c.line_number}.")
        given = {k: v for k, v in c.model_dump(exclude={"line_number"}).items() if k in c.model_fields_set}
        for k, v in given.items():
            setattr(s, "sku_raw" if k == "sku" else k, v)
        s.issues = [i for i in s.issues if i["field"] not in given]
        if s.amount_paid is not None and s.amount_paid < 0:
            s.issues = [
                *s.issues,
                intake.issue("amount_paid", "negative_amount", "A payment line must not be negative."),
            ]
        s.corrected = True
        changed.append({"line_number": c.line_number, "fields": sorted(given)})
    db.flush()
    return changed


def accept(db: Session, who: Who, remittance_id: UUID, body: Any) -> RemittanceDocument:
    doc = get(db, who.tenant_id, remittance_id, lock=True)
    if doc.status != "pending_review":
        raise InvalidState("Only a remittance in review can be accepted.")
    changed = _apply_corrections(db, doc, body)
    if changed:
        _audit(
            db, who.tenant_id, Actor("user", who.user_id), who.meta, Action.REMITTANCE_STAGED, doc,
            after={"corrections": changed},
        )  # fmt: skip
    _accept(db, doc, who.user_id, Actor("user", who.user_id), who.meta, auto=False)
    return doc


def void(db: Session, who: Who, remittance_id: UUID) -> RemittanceDocument:
    doc = get(db, who.tenant_id, remittance_id, lock=True)
    if doc.status not in OPEN:
        raise InvalidState("Only a remittance that is not yet accepted can be voided.")
    _void(db, doc, "rejected_by_user", "Voided by the user.", Actor("user", who.user_id), who.meta)
    return doc


def line_count(db: Session, tenant_id: UUID, remittance_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(RemittanceLine)
            .where(
                RemittanceLine.tenant_id == tenant_id, RemittanceLine.remittance_document_id == remittance_id
            )
        )
        or 0
    )
