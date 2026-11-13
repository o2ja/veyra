# ruff: noqa: S608  # table/column come from a two-entry allow-list; every value is a bound parameter
"""Claim batches (FR-CB-01..04, BUSINESS_LOGIC.md §9.4): build from results or a filter, approve (four-eyes),
export, submit (freeze), void (release).

Concurrency: every batch creation takes one advisory lock per tenant, so two concurrent creates are serial: the
second sees the first's claim lines and answers 409 ``already_claimed``; reference numbering cannot collide. The
partial unique indexes stay the real guard (a violation is mapped to the same 409).

Lines are fixed when the batch is created. Rebate accrual entries (positive delta, BUSINESS_LOGIC.md §5.5) become
``rebate_accrual`` claim lines, and approved adjustments of the manufacturer can be included (S7.4): the batch total
is the lines plus the signed adjustments, ``line_count`` counts lines only.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, FourEyesViolation, InvalidState, NotFound, ValidationFailed
from rebate.core.ids import new_id
from rebate.modules import audit, masterdata, tenancy
from rebate.modules.claims import adjustments, export_jobs, missed, templates
from rebate.modules.claims import batch_schemas as s
from rebate.modules.claims.models import ClaimBatch, ClaimExportTemplate, ClaimLine
from rebate.modules.masterdata import Who, keyset

MAX_LINES = 20_000


class AlreadyClaimed(Conflict):
    code = "already_claimed"
    message = "Some of these lines are already in a claim batch."


class BatchTotalNotPositive(ValidationFailed):
    code = "batch_total_not_positive"
    message = "A claim batch must claim a positive amount."


def _require_positive(db: Session, tenant_id: UUID, manufacturer_id: UUID, total: Decimal) -> None:
    """Owner decision 2026-11-12: a batch totalling <= 0 is refused unless the manufacturer accepts negative claims."""
    rows = masterdata.manufacturer_settings_rows(db, tenant_id, [manufacturer_id])
    if total <= 0 and not (rows and rows[0].allow_negative_claims):
        raise BatchTotalNotPositive(
            details=[
                {
                    "field": "total_amount",
                    "code": "batch_total_not_positive",
                    "message": f"The batch total is {total}: the included adjustments cancel the claim lines.",
                }
            ]
        )


class AuditedFourEyesViolation(FourEyesViolation):
    commit = True  # the audit entry of the refused approval must survive the error response


def _problem(field: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"field": field, "code": code, "message": message, **extra}


def _today(db: Session, tenant_id: UUID) -> date:
    return clock.now().astimezone(ZoneInfo(tenancy.get_settings(db, tenant_id).business_timezone)).date()


def _view(b: ClaimBatch) -> dict[str, Any]:
    return {
        "manufacturer_id": b.manufacturer_id,
        "batch_number": b.batch_number,
        "status": b.status,
        "line_count": b.line_count,
        "total_amount": str(b.total_amount),
        "period_from": b.period_from,
        "period_to": b.period_to,
        "export_template_id": b.export_template_id,
        "exported_file_id": b.exported_file_id,
        "submitted_at": b.submitted_at,
        "external_reference": b.external_reference,
        "void_reason": b.void_reason,
    }


def _audit(db: Session, who: Who, action: audit.Action, b: ClaimBatch, **kw: Any) -> None:
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=action,
        entity_type="claim_batch",
        entity_id=b.id,
        meta=who.meta,
        **kw,
    )


# ---- reads --------------------------------------------------------------------------------------------------


def get(db: Session, tenant_id: UUID, batch_id: UUID, *, lock: bool = False) -> ClaimBatch:
    stmt = select(ClaimBatch).where(ClaimBatch.tenant_id == tenant_id, ClaimBatch.id == batch_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def list_batches(
    db: Session,
    tenant_id: UUID,
    *,
    status: str | None,
    manufacturer_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> list[ClaimBatch]:
    stmt = select(ClaimBatch).where(ClaimBatch.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(ClaimBatch.status == status)
    if manufacturer_id is not None:
        stmt = stmt.where(ClaimBatch.manufacturer_id == manufacturer_id)
    return list(db.scalars(keyset(stmt, ClaimBatch.id, cursor, limit)))


def list_lines(
    db: Session, tenant_id: UUID, batch_id: UUID, *, limit: int, cursor: str | None
) -> list[Row[Any]]:
    get(db, tenant_id, batch_id)
    after = masterdata.decode_cursor(cursor)
    return list(
        db.execute(
            text(
                "SELECT cl.id, cl.batch_id, cl.claim_reference, cl.source_kind, cl.sales_document_line_id, "
                "cl.rebate_accrual_entry_id, cl.calculation_result_id, cl.agreement_version_id, "
                "r.document_number, r.line_number, r.invoice_date, cl.quantity, cl.unit_amount, cl.claim_amount, "
                "cl.frozen_at, cl.released_at, cl.created_at "
                "FROM claim_lines cl "
                "LEFT JOIN calculation_results r ON r.tenant_id = cl.tenant_id AND r.id = cl.calculation_result_id "
                "WHERE cl.tenant_id = :t AND cl.batch_id = :b AND (CAST(:after AS uuid) IS NULL OR cl.id > :after) "
                "ORDER BY cl.id LIMIT :n"
            ),
            {"t": tenant_id, "b": batch_id, "after": after, "n": limit + 1},
        )
    )


# ---- create -------------------------------------------------------------------------------------------------


def _next_number(db: Session, tenant_id: UUID, table: str, column: str, prefix: str) -> int:
    if (table, column) not in (("claim_lines", "claim_reference"), ("claim_batches", "batch_number")):
        raise ValueError("not a numbered column")
    return int(
        db.scalar(
            text(
                f"SELECT COALESCE(MAX(substring({column} FROM '[0-9]+$')::int), 0) FROM {table} "  # nosec B608 (allow-listed identifiers, bound values)
                f"WHERE tenant_id = :t AND {column} LIKE :p"
            ),
            {"t": tenant_id, "p": prefix + "%"},
        )
        or 0
    )


def _claimed(db: Session, tenant_id: UUID, line_ids: list[UUID]) -> list[Row[Any]]:
    """Active claim lines of the lines asked for or of any earlier version of them (BUSINESS_LOGIC §9.4: a re-imported
    line is the same invoice line). ``sales_document_line_id`` in the rows is the line asked for."""
    return list(
        db.execute(
            text(
                "WITH RECURSIVE anc(line_id, source_id) AS ("
                "SELECT x, x FROM unnest(CAST(:ids AS uuid[])) AS x "
                "UNION ALL SELECT anc.line_id, s.id FROM anc "
                "JOIN sales_document_lines s ON s.tenant_id = :t AND s.superseded_by_id = anc.source_id) "
                "SELECT anc.line_id AS sales_document_line_id, cl.claim_reference, b.batch_number, b.id AS batch_id "
                "FROM anc JOIN claim_lines cl ON cl.tenant_id = :t AND cl.sales_document_line_id = anc.source_id "
                "JOIN claim_batches b ON b.tenant_id = cl.tenant_id AND b.id = cl.batch_id "
                "WHERE cl.released_at IS NULL ORDER BY cl.claim_reference"
            ),  # nosec B608 (module constants, bound values)
            {"t": tenant_id, "ids": line_ids},
        )
    )


def _selected_by_results(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, result_ids: list[UUID]
) -> list[Row[Any]]:
    ids = sorted(set(result_ids))
    raw = {
        r.id: r
        for r in db.execute(
            text(
                "SELECT r.id, r.sales_document_line_id, r.manufacturer_id, r.status FROM calculation_results r "
                "JOIN calculation_runs cr ON cr.tenant_id = r.tenant_id AND cr.id = r.run_id AND cr.status = 'completed' "
                "WHERE r.tenant_id = :t AND r.id = ANY(:ids)"
            ),
            {"t": tenant_id, "ids": ids},
        )
    }
    problems = []
    for i in ids:
        r = raw.get(i)
        if r is None:
            problems.append(_problem("result_ids", "not_found", "Unknown result.", result_id=str(i)))
        elif r.manufacturer_id != manufacturer_id:
            problems.append(
                _problem(
                    "result_ids",
                    "wrong_manufacturer",
                    "The result belongs to another manufacturer.",
                    result_id=str(i),
                )
            )
        elif r.status != "eligible":
            problems.append(
                _problem(
                    "result_ids", "not_eligible", "Only eligible results can be claimed.", result_id=str(i)
                )
            )
    if problems:
        raise ValidationFailed(details=problems)
    claimed = _claimed(db, tenant_id, [r.sales_document_line_id for r in raw.values()])
    if claimed:
        by_line = {r.sales_document_line_id: r.id for r in raw.values()}
        raise AlreadyClaimed(
            details=[
                _problem(
                    "result_ids",
                    "already_claimed",
                    "The line is already claimed.",
                    result_id=str(by_line[c.sales_document_line_id]),
                    sales_document_line_id=str(c.sales_document_line_id),
                    claim_reference=c.claim_reference,
                    batch_id=str(c.batch_id),
                    batch_number=c.batch_number,
                )
                for c in claimed
            ]
        )
    rows = missed.missed(db, tenant_id, manufacturer_id=manufacturer_id, result_ids=ids, stable_order=True)
    have = {r.id for r in rows}
    stale = [i for i in ids if i not in have]
    if stale:
        raise ValidationFailed(
            details=[
                _problem(
                    "result_ids",
                    "stale_result",
                    "A newer calculation supersedes this result; use the current one.",
                    result_id=str(i),
                )
                for i in stale
            ]
        )
    return rows


def _claim_line(
    tenant_id: UUID, batch_id: UUID, reference: str, r: Row[Any], now: Any, rebate: bool
) -> ClaimLine:
    common = {
        "tenant_id": tenant_id,
        "id": new_id(),
        "batch_id": batch_id,
        "claim_reference": reference,
        "agreement_version_id": r.agreement_version_id,
        "created_at": now,
    }
    if rebate:  # the accrued delta, claimed as one line (no quantity, no unit amount)
        return ClaimLine(
            **common, source_kind="rebate_accrual", rebate_accrual_entry_id=r.id, claim_amount=r.delta
        )
    return ClaimLine(
        **common,
        source_kind="sales_line",
        sales_document_line_id=r.sales_document_line_id,
        calculation_result_id=r.id,
        quantity=r.eligible_quantity,
        unit_amount=r.claim_unit_amount,
        claim_amount=r.claim_amount,
    )


def _selected_by_accruals(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, entry_ids: list[UUID]
) -> list[Row[Any]]:
    ids = sorted(set(entry_ids))
    raw = {
        r.id: r
        for r in db.execute(
            text(
                "SELECT e.id, e.manufacturer_id, e.agreement_version_id, e.delta, e.period_start, e.period_end "
                "FROM rebate_accrual_entries e JOIN calculation_runs cr ON cr.tenant_id = e.tenant_id "
                "AND cr.id = e.run_id AND cr.status = 'completed' "
                "WHERE e.tenant_id = :t AND e.id = ANY(:ids)"
            ),
            {"t": tenant_id, "ids": ids},
        )
    }
    problems = []
    for i in ids:
        r = raw.get(i)
        if r is None:
            problems.append(
                _problem("accrual_entry_ids", "not_found", "Unknown accrual entry.", entry_id=str(i))
            )
        elif r.manufacturer_id != manufacturer_id:
            problems.append(
                _problem(
                    "accrual_entry_ids",
                    "wrong_manufacturer",
                    "The accrual entry belongs to another manufacturer.",
                    entry_id=str(i),
                )
            )
        elif r.delta <= 0:
            problems.append(
                _problem(
                    "accrual_entry_ids",
                    "not_claimable",
                    "Only a positive accrual delta can be claimed; a negative one is a rebate_clawback adjustment.",
                    entry_id=str(i),
                )
            )
    if problems:
        raise ValidationFailed(details=problems)
    claimed = db.execute(
        text(
            "SELECT cl.rebate_accrual_entry_id, cl.claim_reference, b.batch_number, b.id AS batch_id "
            "FROM claim_lines cl JOIN claim_batches b ON b.tenant_id = cl.tenant_id AND b.id = cl.batch_id "
            "WHERE cl.tenant_id = :t AND cl.rebate_accrual_entry_id = ANY(:ids) AND cl.released_at IS NULL "
            "ORDER BY cl.claim_reference"
        ),
        {"t": tenant_id, "ids": ids},
    ).all()
    if claimed:
        raise AlreadyClaimed(
            details=[
                _problem(
                    "accrual_entry_ids",
                    "already_claimed",
                    "The accrual entry is already claimed.",
                    entry_id=str(c.rebate_accrual_entry_id),
                    claim_reference=c.claim_reference,
                    batch_id=str(c.batch_id),
                    batch_number=c.batch_number,
                )
                for c in claimed
            ]
        )
    return sorted(raw.values(), key=lambda r: (r.period_start, r.id))


def create(db: Session, who: Who, body: s.BatchIn) -> ClaimBatch:
    t = who.tenant_id
    if masterdata.existing_manufacturer_ids(db, t, [body.manufacturer_id]) != {body.manufacturer_id}:
        raise ValidationFailed(details=[_problem("manufacturer_id", "not_found", "Unknown manufacturer.")])
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"claims:{t}"})
    rebate = body.accrual_entry_ids is not None
    if body.result_ids is not None:
        rows = _selected_by_results(db, t, body.manufacturer_id, body.result_ids)
    elif body.accrual_entry_ids is not None:
        rows = _selected_by_accruals(db, t, body.manufacturer_id, body.accrual_entry_ids)
    else:
        f = body.filter or s.BatchFilter()  # the request model guarantees one of the two selections
        rows = missed.missed(
            db,
            t,
            manufacturer_id=body.manufacturer_id,
            agreement_id=f.agreement_id,
            invoice_from=f.invoice_from,
            invoice_to=f.invoice_to,
            stable_order=True,
            limit=MAX_LINES + 1,
        )
        if len(rows) > MAX_LINES:
            raise ValidationFailed(
                details=[
                    _problem("filter", "too_many_lines", f"More than {MAX_LINES} lines: narrow the filter.")
                ]
            )
        if not rows:
            raise ValidationFailed(
                details=[_problem("filter", "no_lines", "No eligible unclaimed lines match.")]
            )
    year = _today(db, t).year
    base_batch = _next_number(db, t, "claim_batches", "batch_number", f"CB-{year}-")
    base_line = _next_number(db, t, "claim_lines", "claim_reference", f"CL-{year}-")
    included = (
        adjustments.lock_for_inclusion(db, t, body.manufacturer_id, body.include_adjustment_ids)
        if body.include_adjustment_ids
        else []
    )
    lines_total = sum((Decimal(r.delta if rebate else r.claim_amount) for r in rows), Decimal(0))
    total = lines_total + adjustments.total(included)
    _require_positive(db, t, body.manufacturer_id, total)
    now = clock.now()
    batch = ClaimBatch(
        tenant_id=t,
        id=new_id(),
        manufacturer_id=body.manufacturer_id,
        batch_number=f"CB-{year}-{base_batch + 1:06d}",
        status="draft",
        period_from=min(r.period_start if rebate else r.invoice_date for r in rows),
        period_to=max(r.period_end if rebate else r.invoice_date for r in rows),
        line_count=len(rows),
        total_amount=total,
        created_by=who.user_id,
        created_at=now,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        _claim_line(t, batch.id, f"CL-{year}-{base_line + n:06d}", r, now, rebate)
        for n, r in enumerate(rows, start=1)
    )
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:  # the unique index caught a double claim the pre-check could not see
        raise AlreadyClaimed() from None
    adjustments.include(db, who, included, batch.id)
    after = _view(batch)
    if included:
        after |= {
            "included_adjustment_ids": [str(a.id) for a in included],
            "adjustments_total": str(adjustments.total(included)),
        }
    _audit(db, who, audit.Action.CLAIM_BATCH_CREATED, batch, after=after)
    return batch


# ---- workflow -----------------------------------------------------------------------------------------------


def approve(db: Session, who: Who, batch_id: UUID) -> ClaimBatch:
    b = get(db, who.tenant_id, batch_id, lock=True)
    if b.status != "draft":
        raise InvalidState("Only a draft batch can be approved.")
    if tenancy.get_settings(db, who.tenant_id).four_eyes_required and who.user_id == b.created_by:
        _audit(db, who, audit.Action.CLAIM_BATCH_FOUR_EYES_VIOLATION, b, after=_view(b))
        raise AuditedFourEyesViolation()
    _require_positive(db, who.tenant_id, b.manufacturer_id, b.total_amount)
    before = _view(b)
    b.status, b.approved_by, b.approved_at = "approved", who.user_id, clock.now()
    db.flush()
    _audit(db, who, audit.Action.CLAIM_BATCH_APPROVED, b, before=before, after=_view(b))
    return b


def submit(db: Session, who: Who, batch_id: UUID, submitted_at: date, external_reference: str) -> ClaimBatch:
    """Freeze the lines and mark the batch submitted. Sending the same details again returns the batch unchanged."""
    b = get(db, who.tenant_id, batch_id, lock=True)
    if b.status == "submitted":
        if b.submitted_at == submitted_at and b.external_reference == external_reference:
            return b
        raise InvalidState("The batch was already submitted with other details.")
    if b.status != "approved":
        raise InvalidState("Only an approved batch can be submitted.")
    if submitted_at > _today(db, who.tenant_id):
        raise ValidationFailed(
            details=[_problem("submitted_at", "in_future", "The submission date cannot be in the future.")]
        )
    _require_positive(db, who.tenant_id, b.manufacturer_id, b.total_amount)
    before = _view(b)
    b.status, b.submitted_at, b.submitted_by, b.external_reference = (
        "submitted",
        submitted_at,
        who.user_id,
        external_reference,
    )
    db.flush()
    db.execute(
        update(ClaimLine)
        .where(ClaimLine.tenant_id == who.tenant_id, ClaimLine.batch_id == b.id)
        .values(frozen_at=clock.now())
        .execution_options(synchronize_session=False)
    )
    _audit(db, who, audit.Action.CLAIM_BATCH_SUBMITTED, b, before=before, after=_view(b))
    return b


def void(db: Session, who: Who, batch_id: UUID, reason: str) -> ClaimBatch:
    b = get(db, who.tenant_id, batch_id, lock=True)
    if b.status not in ("draft", "approved"):
        raise InvalidState("Only a draft or approved batch can be voided.")
    before = _view(b)
    now = clock.now()
    b.status, b.void_reason, b.voided_at = "void", reason, now
    db.flush()
    db.execute(
        update(ClaimLine)
        .where(ClaimLine.tenant_id == who.tenant_id, ClaimLine.batch_id == b.id)
        .values(released_at=now)
        .execution_options(synchronize_session=False)
    )
    adjustments.release(db, who, b.id)  # its approved adjustments go back to the queue
    _audit(db, who, audit.Action.CLAIM_BATCH_VOIDED, b, before=before, after=_view(b))
    return b


# ---- export requests ----------------------------------------------------------------------------------------


def request_batch_export(
    db: Session, who: Who, batch_id: UUID, template_id: UUID | None
) -> tuple[UUID, UUID]:
    b = get(db, who.tenant_id, batch_id)
    if b.status not in ("approved", "submitted"):
        raise InvalidState("Approve the batch before exporting it.")
    payload = {"kind": "batch", "batch_id": str(b.id)}
    if template_id is not None:
        tpl = templates.get(db, who.tenant_id, template_id)
        if tpl.manufacturer_id != b.manufacturer_id or not tpl.active:
            raise ValidationFailed(
                details=[
                    _problem(
                        "template_id", "not_usable", "Use an active template of the batch's manufacturer."
                    )
                ]
            )
        payload["template_id"] = str(tpl.id)
    else:  # the manufacturer's newest active template; none -> the built-in CSV layout
        newest = db.scalar(
            select(ClaimExportTemplate.id)
            .where(
                ClaimExportTemplate.tenant_id == who.tenant_id,
                ClaimExportTemplate.manufacturer_id == b.manufacturer_id,
                ClaimExportTemplate.active.is_(True),
            )
            .order_by(ClaimExportTemplate.id.desc())
            .limit(1)
        )
        if newest is not None:
            payload["template_id"] = str(newest)
    file_id, job_id = export_jobs.enqueue_export(db, who.tenant_id, who.user_id, payload)
    _audit(
        db,
        who,
        audit.Action.CLAIM_BATCH_EXPORT_REQUESTED,
        b,
        after={"export_template_id": payload.get("template_id")},
    )
    return file_id, job_id


def request_missed_export(db: Session, who: Who, fmt: str, filt: dict[str, str]) -> tuple[UUID, UUID]:
    file_id, job_id = export_jobs.enqueue_export(
        db, who.tenant_id, who.user_id, {"kind": "missed", "format": fmt, **filt}
    )
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=audit.Action.MISSED_CLAIMS_EXPORT_REQUESTED,
        entity_type="missed_claims_export",
        entity_id=file_id,
        after={"format": fmt, "filter": filt},
        meta=who.meta,
    )
    return file_id, job_id
