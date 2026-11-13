"""Case service (S9.2; BUSINESS_LOGIC.md §10.3, §12, §12.1; DATABASE_SCHEMA.md §3.10).

Every status change writes a ``variance_case_events`` row and an audit entry in the caller's transaction. The state
machine, deadlines and four-eyes rules live in the pure ``domain`` package; this module loads, locks, calls them and
persists. Amounts are never computed here: a case carries the finding's amount, a reclaim carries the case's open
variance.
"""

import base64
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import (
    Conflict,
    FourEyesViolation,
    InvalidState,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from rebate.core.ids import new_id
from rebate.core.jobs import enqueue
from rebate.modules import audit, claims, files, tenancy
from rebate.modules.masterdata import Who, keyset
from rebate.modules.reconciliation import FinalisedRemittance, Finding
from rebate.modules.recovery import assessment
from rebate.modules.recovery.domain import deadlines
from rebate.modules.recovery.domain import states as st
from rebate.modules.recovery.models import CaseAttachment, Notification, VarianceCase, VarianceCaseEvent

LIVE = (st.CaseStatus.OPEN, st.CaseStatus.TO_RECLAIM, st.CaseStatus.SUBMITTED)
RECLAIMABLE_TYPES = ("short_paid", "rejected", "unpaid")
AT_RISK_DAYS = 14
SWEEP_JOB = "recovery_sweep"


class AuditedFourEyesViolation(FourEyesViolation):
    commit = True  # the audit entry of the refused write-off must survive the error response


class DedicatedEndpoint(ValidationFailed):
    pass


# ---- helpers ------------------------------------------------------------------------------------------------


def business_today(db: Session, tenant_id: UUID) -> date:
    return deadlines.tenant_today(clock.now(), tenancy.get_settings(db, tenant_id).business_timezone)


def view(c: VarianceCase) -> dict[str, Any]:
    # "reason", not "reason_code": the audit log refuses any key that looks like a secret ("code"), see test_audit_logs_config
    return {
        "case_type": c.case_type,
        "reason": c.reason_code,
        "status": c.status,
        "claim_line_id": c.claim_line_id,
        "remittance_line_id": c.remittance_line_id,
        "variance_amount": str(c.variance_amount),
        "recovered_amount": str(c.recovered_amount),
        "written_off_amount": str(c.written_off_amount),
        "owner_user_id": c.owner_user_id,
        "deadline_date": c.deadline_date,
        "write_off_proposed_by": c.write_off_proposed_by,
        "write_off_reason": c.write_off_reason,
        "revision": c.revision,
    }


def _audit(db: Session, who: Who, action: audit.Action, c: VarianceCase, **kw: Any) -> None:
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=action,
        entity_type="variance_case",
        entity_id=c.id,
        meta=who.meta,
        **kw,
    )


def _event(
    db: Session,
    c: VarianceCase,
    event_type: str,
    actor: UUID | None,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        VarianceCaseEvent(
            tenant_id=c.tenant_id,
            id=new_id(),
            case_id=c.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            note=note,
            payload=payload,
            actor_user_id=actor,
            created_at=clock.now(),
        )
    )
    db.flush()


def get(db: Session, tenant_id: UUID, case_id: UUID, *, lock: bool = False) -> VarianceCase:
    stmt = select(VarianceCase).where(VarianceCase.tenant_id == tenant_id, VarianceCase.id == case_id)
    c = db.scalar(stmt.with_for_update() if lock else stmt)
    if c is None:
        raise NotFound()
    return c


def _locked(db: Session, who: Who, case_id: UUID, revision: int | None) -> VarianceCase:
    c = get(db, who.tenant_id, case_id, lock=True)
    if revision is not None and c.revision != revision:
        raise PreconditionFailed()
    return c


def _change(db: Session, c: VarianceCase, **values: Any) -> None:
    """Apply ``values`` and bump the revision (the database refuses an update that does not)."""
    if c.write_off_proposed_by is not None and (
        "recovered_amount" in values or values.get("status", c.status) not in (c.status, "written_off")
    ):  # a four-eyes proposal is for the outstanding amount at that time: any recovery or move voids it
        values = {
            "write_off_proposed_by": None,
            "write_off_proposed_at": None,
            "write_off_reason": None,
            **values,
        }
    for k, v in values.items():
        setattr(c, k, v)
    c.revision += 1
    db.flush()


def _move(
    db: Session,
    who: Who | None,
    c: VarianceCase,
    target: st.CaseStatus,
    note: str | None = None,
    **extra: Any,
) -> None:
    """Record a status change: row update, event and audit in the caller's transaction."""
    before, previous = view(c), c.status
    closed = {"closed_at": clock.now()} if target not in LIVE else {}
    _change(db, c, status=target.value, **closed, **extra)
    _event(db, c, "status_changed", who.user_id if who else None, from_status=previous,
           to_status=target.value, note=note)  # fmt: skip
    if who is not None:
        _audit(db, who, audit.Action.VARIANCE_CASE_STATUS_CHANGED, c, before=before, after=view(c))


def _status(c: VarianceCase) -> st.CaseStatus:
    return st.CaseStatus(c.status)


def _refuse(exc: st.TransitionError) -> InvalidState:
    return InvalidState(str(exc), details=[{"field": "to_status", "code": exc.code, "message": str(exc)}])


def _is_user_of_tenant(db: Session, tenant_id: UUID, user_id: UUID) -> bool:
    return bool(
        db.scalar(
            text("SELECT 1 FROM memberships WHERE tenant_id = :t AND user_id = :u AND status = 'active'"),
            {"t": tenant_id, "u": user_id},
        )
    )


# ---- creation from a finalised remittance (S9.2; called by reconciliation's finalise) ----------------------------


def _case_deadline(
    db: Session, tenant_id: UUID, rem: FinalisedRemittance, f: Finding
) -> tuple[date | None, str | None]:
    """Dispute / re-claim deadline per the manufacturer's setting (§12); ``(None, None)`` = ``deadline_unknown``."""
    s = db.execute(
        text(
            "SELECT dispute_window_days, dispute_window_basis FROM manufacturer_settings "
            "WHERE tenant_id = :t AND manufacturer_id = :m"
        ),
        {"t": tenant_id, "m": rem.manufacturer_id},
    ).one_or_none()
    if s is None or s.dispute_window_days is None:
        return None, None
    base: date | None
    if s.dispute_window_basis == "remittance_date":
        # a finding without a remittance line (``unpaid``) has no remittance to count from: deadline unknown
        base = rem.document_date if f.remittance_line_id is not None else None
    else:
        base = db.scalar(
            text(
                "SELECT b.submitted_at FROM claim_lines c JOIN claim_batches b "
                "ON b.tenant_id = c.tenant_id AND b.id = c.batch_id WHERE c.tenant_id = :t AND c.id = :c"
            ),
            {"t": tenant_id, "c": f.claim_line_id},
        )
    due = deadlines.dispute_deadline(base, s.dispute_window_days)
    return (due, s.dispute_window_basis) if due is not None else (None, None)


def open_cases(db: Session, who: Who, rem: FinalisedRemittance) -> list[VarianceCase]:
    """Open one case per finding (a finding whose live case already exists, e.g. a repeated ``unpaid``, is
    skipped), then record recoveries the remittance carries for reclaim adjustments. Same transaction as the
    finalisation; a failure rolls the finalisation back."""
    t = who.tenant_id
    made: list[VarianceCase] = []
    for f in rem.findings:
        if f.amount <= 0:
            raise ValidationFailed("A finding's amount is positive.")
        due, basis = _case_deadline(db, t, rem, f)
        row = db.execute(
            insert(VarianceCase)
            .values(
                tenant_id=t,
                id=new_id(),
                manufacturer_id=rem.manufacturer_id,
                claim_line_id=f.claim_line_id,
                remittance_line_id=f.remittance_line_id,
                remittance_document_id=rem.remittance_id,
                case_type=f.case_type,
                reason_code=f.reason_code,
                external_reason_code=f.external_reason_code,
                claimed_amount=f.claimed,
                paid_amount=f.paid,
                variance_amount=f.amount,
                deadline_date=due,
                deadline_basis=basis,
                dispute_assessment=assessment.assess_case(
                    db, t, f.claim_line_id, f.remittance_line_id, f.reason_code
                ),
                created_by=who.user_id,
                created_at=clock.now(),
            )
            .on_conflict_do_nothing()
            .returning(VarianceCase.id)
        ).scalar_one_or_none()
        if row is None:
            continue
        c = get(db, t, row)
        db.refresh(c)
        _event(
            db,
            c,
            "created",
            who.user_id,
            payload={"remittance_id": str(rem.remittance_id), "deadline_unknown": due is None},
        )
        _audit(db, who, audit.Action.VARIANCE_CASE_CREATED, c, after=view(c))
        made.append(c)
    record_recoveries(db, who, rem.remittance_id)
    if made:
        schedule_sweep(db, t, business_today(db, t))
    return made


def record_recoveries(db: Session, who: Who, remittance_id: UUID) -> None:
    """Confirmed allocations of this remittance to a case's reclaim adjustment are recovered money (§12.1).
    A ``to_reclaim`` case that is being paid was evidently submitted: it moves to ``submitted`` first. Applied once
    per remittance and case (an ``amount_recovered`` event carries the remittance id)."""
    t = who.tenant_id
    rows = db.execute(
        text(
            "SELECT a.variance_case_id AS case_id, sum(m.allocated_amount) AS paid "
            "FROM reconciliation_matches m "
            "JOIN remittance_lines l ON l.tenant_id = m.tenant_id AND l.id = m.remittance_line_id "
            "JOIN claim_adjustments a ON a.tenant_id = m.tenant_id AND a.id = m.claim_adjustment_id "
            "WHERE m.tenant_id = :t AND m.status = 'confirmed' AND l.remittance_document_id = :r "
            "AND a.adjustment_type = 'recovery_reclaim' GROUP BY a.variance_case_id ORDER BY a.variance_case_id"
        ),
        {"t": t, "r": remittance_id},
    ).all()
    for r in rows:
        c = get(db, t, r.case_id, lock=True)
        if db.scalar(
            select(func.count()).where(
                VarianceCaseEvent.tenant_id == t,
                VarianceCaseEvent.case_id == c.id,
                VarianceCaseEvent.event_type == "amount_recovered",
                VarianceCaseEvent.payload["remittance_id"].astext == str(remittance_id),
            )
        ):
            continue
        if _status(c) not in (st.CaseStatus.TO_RECLAIM, st.CaseStatus.SUBMITTED):
            continue  # closed meanwhile: the match stays visible on the remittance, the case is final
        if _status(c) is st.CaseStatus.TO_RECLAIM:
            _move(db, who, c, st.CaseStatus.SUBMITTED, "Payment received for the reclaim")
        total, target = st.apply_recovery(_status(c), c.variance_amount, c.recovered_amount, r.paid)
        before = view(c)
        extra = (
            {"status": target.value, "closed_at": clock.now()} if target is st.CaseStatus.RECOVERED else {}
        )
        _change(db, c, recovered_amount=total, **extra)
        _event(db, c, "amount_recovered", who.user_id, to_status=None,
               payload={"remittance_id": str(remittance_id), "amount": str(r.paid), "recovered_total": str(total)})  # fmt: skip
        if target is st.CaseStatus.RECOVERED:
            _event(db, c, "status_changed", who.user_id, from_status="submitted", to_status="recovered")
        _audit(db, who, audit.Action.VARIANCE_CASE_RECOVERY_RECORDED, c, before=before, after=view(c))


# ---- reads --------------------------------------------------------------------------------------------------


def _encode(c: VarianceCase) -> str:
    raw = f"{c.deadline_date.isoformat() if c.deadline_date else '-'}|{c.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[date | None, UUID]:
    try:
        d, i = base64.urlsafe_b64decode(cursor.encode() + b"==").decode().split("|")
        return (None if d == "-" else date.fromisoformat(d)), UUID(i)
    except ValueError:
        raise ValidationFailed(
            details=[{"field": "cursor", "code": "invalid", "message": "Invalid cursor."}]
        ) from None


def list_cases(
    db: Session,
    tenant_id: UUID,
    *,
    statuses: list[str] | None,
    manufacturer_id: UUID | None,
    owner_user_id: UUID | None,
    deadline_before: date | None,
    reason_code: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[VarianceCase], str | None]:
    """The queue: unknown deadlines first, then by deadline, then id."""
    stmt: Select[Any] = select(VarianceCase).where(VarianceCase.tenant_id == tenant_id)
    if statuses:
        stmt = stmt.where(VarianceCase.status.in_(statuses))
    if manufacturer_id:
        stmt = stmt.where(VarianceCase.manufacturer_id == manufacturer_id)
    if owner_user_id:
        stmt = stmt.where(VarianceCase.owner_user_id == owner_user_id)
    if deadline_before:
        stmt = stmt.where(VarianceCase.deadline_date < deadline_before)
    if reason_code:
        stmt = stmt.where(VarianceCase.reason_code == reason_code)
    if cursor:
        d, i = _decode(cursor)
        if d is None:
            stmt = stmt.where(
                or_(
                    and_(VarianceCase.deadline_date.is_(None), VarianceCase.id > i),
                    VarianceCase.deadline_date.is_not(None),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    VarianceCase.deadline_date > d, and_(VarianceCase.deadline_date == d, VarianceCase.id > i)
                )
            )
    rows = list(
        db.scalars(
            stmt.order_by(VarianceCase.deadline_date.asc().nulls_first(), VarianceCase.id).limit(limit + 1)
        )
    )
    more = len(rows) > limit
    data = rows[:limit]
    return data, (_encode(data[-1]) if more and data else None)


def summary(db: Session, tenant_id: UUID) -> dict[str, Any]:
    """Totals by status and reason; ``at_risk`` = open variance of live cases whose deadline is unknown or within
    ``AT_RISK_DAYS`` days of the tenant's business date (overdue included)."""
    horizon = business_today(db, tenant_id) + timedelta(days=AT_RISK_DAYS)
    rem = VarianceCase.variance_amount - VarianceCase.recovered_amount
    live = VarianceCase.status.in_([s.value for s in LIVE])

    def group(col: Any) -> list[dict[str, Any]]:
        q = select(
            col,
            func.count(),
            func.sum(VarianceCase.variance_amount),
            func.sum(VarianceCase.recovered_amount),
            func.sum(VarianceCase.written_off_amount),
        )
        return [
            {"key": k, "count": n, "variance_amount": v, "recovered_amount": r, "written_off_amount": w}
            for k, n, v, r, w in db.execute(
                q.where(VarianceCase.tenant_id == tenant_id).group_by(col).order_by(col)
            )
        ]

    at_risk = db.execute(
        select(func.count(), func.coalesce(func.sum(rem), 0)).where(
            VarianceCase.tenant_id == tenant_id,
            live,
            or_(VarianceCase.deadline_date.is_(None), VarianceCase.deadline_date <= horizon),
        )
    ).one()
    return {
        "by_status": group(VarianceCase.status),
        "by_reason": group(VarianceCase.reason_code),
        "at_risk": {"count": at_risk[0], "amount": at_risk[1], "deadline_within_days": AT_RISK_DAYS},
    }


def list_events(db: Session, tenant_id: UUID, case_id: UUID) -> list[VarianceCaseEvent]:
    get(db, tenant_id, case_id)
    return list(
        db.scalars(
            select(VarianceCaseEvent)
            .where(VarianceCaseEvent.tenant_id == tenant_id, VarianceCaseEvent.case_id == case_id)
            .order_by(VarianceCaseEvent.created_at, VarianceCaseEvent.id)
        )
    )


# ---- work on a case -----------------------------------------------------------------------------------------


def assign(db: Session, who: Who, case_id: UUID, owner_user_id: UUID | None, revision: int) -> VarianceCase:
    c = _locked(db, who, case_id, revision)
    if _status(c) not in LIVE:
        raise InvalidState("A closed case cannot be reassigned.")
    if owner_user_id is not None and not _is_user_of_tenant(db, who.tenant_id, owner_user_id):
        raise ValidationFailed(
            details=[
                {
                    "field": "owner_user_id",
                    "code": "not_a_member",
                    "message": "Not a member of this workspace.",
                }
            ]
        )
    before = view(c)
    _change(db, c, owner_user_id=owner_user_id)
    _event(
        db,
        c,
        "assigned",
        who.user_id,
        payload={"owner_user_id": str(owner_user_id) if owner_user_id else None},
    )
    _audit(db, who, audit.Action.VARIANCE_CASE_ASSIGNED, c, before=before, after=view(c))
    return c


def add_note(db: Session, who: Who, case_id: UUID, note: str) -> VarianceCaseEvent:
    c = get(db, who.tenant_id, case_id)
    _event(db, c, "note", who.user_id, note=note)
    _audit(db, who, audit.Action.VARIANCE_CASE_NOTE_ADDED, c, after=view(c))
    return db.scalars(
        select(VarianceCaseEvent)
        .where(VarianceCaseEvent.tenant_id == who.tenant_id, VarianceCaseEvent.case_id == c.id)
        .order_by(VarianceCaseEvent.id.desc())
        .limit(1)
    ).one()


def add_attachment(db: Session, who: Who, case_id: UUID, file_id: UUID) -> CaseAttachment:
    """Only a clean-scanned ``attachment`` file of this workspace (the database trigger checks it too)."""
    c = get(db, who.tenant_id, case_id)
    f = files.get(db, who.tenant_id, file_id)  # 404 for a foreign or missing file
    if f.kind != "attachment" or f.scan_status != "clean":
        raise Conflict(
            "Only a file uploaded as an attachment and scanned clean can be attached.",
            details=[
                {
                    "field": "file_id",
                    "code": "not_attachable",
                    "message": f"kind {f.kind}, scan {f.scan_status}",
                }
            ],
        )
    a = CaseAttachment(
        tenant_id=who.tenant_id,
        id=new_id(),
        case_id=c.id,
        file_id=f.id,
        added_by=who.user_id,
        added_at=clock.now(),
    )
    if db.scalar(
        select(func.count()).where(
            CaseAttachment.tenant_id == who.tenant_id,
            CaseAttachment.case_id == c.id,
            CaseAttachment.file_id == f.id,
        )
    ):
        raise Conflict("This file is already attached.")
    db.add(a)
    db.flush()
    _event(db, c, "attachment", who.user_id, payload={"file_id": str(f.id), "name": f.original_name})
    _audit(db, who, audit.Action.VARIANCE_CASE_ATTACHMENT_ADDED, c, after=view(c))
    return a


def list_attachments(db: Session, tenant_id: UUID, case_id: UUID) -> list[CaseAttachment]:
    get(db, tenant_id, case_id)
    return list(
        db.scalars(
            select(CaseAttachment)
            .where(CaseAttachment.tenant_id == tenant_id, CaseAttachment.case_id == case_id)
            .order_by(CaseAttachment.id)
        )
    )


MANUAL_TARGETS = {st.CaseStatus.CLOSED_NO_ACTION, st.CaseStatus.SUBMITTED}


def transition(
    db: Session, who: Who, case_id: UUID, to_status: st.CaseStatus, note: str | None, revision: int
) -> VarianceCase:
    """User-driven moves: ``open -> closed_no_action`` and ``to_reclaim -> submitted``. The other edges have their own
    endpoint (reclaim, write-off) or follow from matched payments (recovered)."""
    c = _locked(db, who, case_id, revision)
    try:
        st.check_transition(_status(c), to_status, st.Actor(str(who.user_id), False))
    except st.TransitionError as exc:
        raise _refuse(exc) from None
    if to_status not in MANUAL_TARGETS:
        raise DedicatedEndpoint(
            details=[{"field": "to_status", "code": "use_dedicated_endpoint",
                      "message": "Use /reclaim, /write-off or a matched payment for this status."}]
        )  # fmt: skip
    _move(db, who, c, to_status, note)
    return c


def reclaim(db: Session, who: Who, case_id: UUID, revision: int) -> tuple[VarianceCase, UUID]:
    """Ask to claim the open variance again: a ``recovery_reclaim`` adjustment proposal through the claims service,
    and ``open``/``submitted`` -> ``to_reclaim``. The adjustment then follows the ordinary approve/batch flow."""
    c = _locked(db, who, case_id, revision)
    if c.case_type not in RECLAIMABLE_TYPES or c.claim_line_id is None:
        raise InvalidState(
            "Only a short-paid, rejected or unpaid case on a claim line can be claimed again.",
            details=[{"field": "case_type", "code": "not_reclaimable", "message": c.case_type}],
        )
    try:
        st.check_transition(_status(c), st.CaseStatus.TO_RECLAIM, st.Actor(str(who.user_id), False))
    except st.TransitionError as exc:
        raise _refuse(exc) from None
    open_amount = c.variance_amount - c.recovered_amount
    adj = claims.propose_recovery_reclaim(
        db,
        who,
        manufacturer_id=c.manufacturer_id,
        claim_line_id=c.claim_line_id,
        variance_case_id=c.id,
        amount=open_amount,
        reason=f"Recovery of {c.case_type} case ({c.reason_code}): open variance {open_amount}",
    )
    _event(
        db,
        c,
        "reclaim_proposed",
        who.user_id,
        payload={"adjustment_id": str(adj.id), "amount": str(open_amount)},
    )
    _audit(db, who, audit.Action.VARIANCE_CASE_RECLAIM_REQUESTED, c, after=view(c))
    _move(db, who, c, st.CaseStatus.TO_RECLAIM, "Reclaim proposed")
    return c, adj.id


def write_off(db: Session, who: Who, case_id: UUID, reason: str, revision: int) -> VarianceCase:
    """Approver-only (the route requires ``case.write_off``). With the workspace's four-eyes setting on, the first
    call proposes and a *different* approver's call writes off; with it off, one call writes off."""
    c = _locked(db, who, case_id, revision)
    four = tenancy.get_settings(db, who.tenant_id).four_eyes_required
    if _status(c) not in LIVE:
        raise InvalidState("A closed case cannot be written off.")
    actor = st.Actor(str(who.user_id), True)
    proposer = str(c.write_off_proposed_by) if c.write_off_proposed_by else None
    if four and proposer is None:
        before = view(c)
        _change(
            db,
            c,
            write_off_proposed_by=who.user_id,
            write_off_proposed_at=clock.now(),
            write_off_reason=reason,
        )
        _event(db, c, "write_off_proposed", who.user_id, note=reason)
        _audit(db, who, audit.Action.VARIANCE_CASE_WRITE_OFF_PROPOSED, c, before=before, after=view(c))
        return c
    try:
        st.check_transition(
            _status(c), st.CaseStatus.WRITTEN_OFF, actor, write_off_proposed_by=proposer, four_eyes=four
        )
    except st.FourEyesViolation:
        _audit(db, who, audit.Action.VARIANCE_CASE_FOUR_EYES_VIOLATION, c, after=view(c))
        raise AuditedFourEyesViolation() from None
    except st.TransitionError as exc:
        raise _refuse(exc) from None
    claims.retire_recovery_reclaims(
        db, who, c.id
    )  # same transaction: no reclaim can be batched after the write-off
    _move(
        db, who, c, st.CaseStatus.WRITTEN_OFF, reason,
        written_off_by=who.user_id, written_off_at=clock.now(),
        written_off_amount=c.variance_amount - c.recovered_amount,
        **({"write_off_reason": reason} if c.write_off_reason is None else {}),  # four-eyes off: no proposal made it
    )  # fmt: skip
    _audit(db, who, audit.Action.VARIANCE_CASE_WRITTEN_OFF, c, after=view(c))
    return c


# ---- sweep scheduling (the job itself is in jobs.py) ----------------------------------------------------------


def schedule_sweep(db: Session, tenant_id: UUID, day: date, run_at: datetime | None = None) -> None:
    """At most one sweep per tenant and business day (dedupe key); the first is enqueued when cases appear."""
    enqueue(
        db,
        SWEEP_JOB,
        tenant_id=tenant_id,
        payload={},
        dedupe_key=f"{SWEEP_JOB}:{tenant_id}:{day.isoformat()}",
        run_at=run_at,
    )


# ---- notifications (the caller's own) ---------------------------------------------------------------------------


def list_notifications(
    db: Session, tenant_id: UUID, user_id: UUID, *, unread: bool, limit: int, cursor: str | None
) -> list[Notification]:
    stmt = select(Notification).where(Notification.tenant_id == tenant_id, Notification.user_id == user_id)
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    return list(db.scalars(keyset(stmt, Notification.id, cursor, limit)))


def mark_read(db: Session, tenant_id: UUID, user_id: UUID, notification_id: UUID) -> Notification:
    """Another user's notification looks exactly like a missing one."""
    n = db.scalar(
        select(Notification)
        .where(
            Notification.tenant_id == tenant_id,
            Notification.user_id == user_id,
            Notification.id == notification_id,
        )
        .with_for_update()
    )
    if n is None:
        raise NotFound()
    if n.read_at is None:
        n.read_at = clock.now()
        db.flush()
    return n
