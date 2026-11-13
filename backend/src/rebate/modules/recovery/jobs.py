"""S9.4 jobs (BUSINESS_LOGIC.md §12): the daily deadline sweep and the per-user email digest.

* ``recovery_sweep`` (tenant-scoped, once per business day): for every live case with a deadline, the reminder due
  today (14/7/3 days before, deduped per case, kind and offset) becomes an in-app notification for the case owner,
  or for every active admin and approver while it has none; users with unsent notifications get a digest job; the
  next day's sweep is enqueued (the chain starts when the first case is opened).
* ``recovery_digest`` (tenant-scoped, one per user and day): one email listing the user's unsent notifications,
  through ``core.mail``; ``emailed_at`` is set only after the message was accepted, so a failed send is retried.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError, enqueue, job
from rebate.core.mail import Mail, deliver
from rebate.modules import tenancy
from rebate.modules.recovery import service
from rebate.modules.recovery.domain import deadlines
from rebate.modules.recovery.domain import notifications as dn
from rebate.modules.recovery.models import Notification, VarianceCase

DIGEST_JOB = "recovery_digest"
SWEEP_AT = time(0, 5)


def _tenant(ctx: JobContext) -> UUID:
    if ctx.tenant_id is None:  # unreachable: the queue refuses a tenant-scoped job without a tenant
        raise PermanentError("tenant-scoped job without tenant context")
    return ctx.tenant_id


def _recipients(db: Session, tenant: UUID, owner: UUID | None) -> list[UUID]:
    """The owner while they are an active member, else every active admin and approver."""
    if owner is not None and db.scalar(
        text("SELECT 1 FROM memberships WHERE tenant_id = :t AND user_id = :u AND status = 'active'"),
        {"t": tenant, "u": owner},
    ):
        return [owner]
    return list(
        db.scalars(
            text(
                "SELECT user_id FROM memberships WHERE tenant_id = :t AND status = 'active' "
                "AND role IN ('admin', 'approver') ORDER BY user_id"
            ),
            {"t": tenant},
        )
    )


def _title(case_type: str, amount: object, manufacturer: str, days: int) -> str:
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    return f"{case_type.replace('_', ' ').capitalize()} {amount:.2f} ({manufacturer}): deadline {when}"


@job(service.SWEEP_JOB, tenant_scoped=True, on_dead=lambda db, ctx: _sweep_died(db, ctx))
def recovery_sweep(db: Session, ctx: JobContext) -> None:
    tenant = _tenant(ctx)
    today = service.business_today(db, tenant)
    cases = db.scalars(
        select(VarianceCase)
        .where(
            VarianceCase.tenant_id == tenant,
            VarianceCase.status.in_([s.value for s in service.LIVE]),
            VarianceCase.deadline_date.is_not(None),
        )
        .order_by(VarianceCase.id)
    ).all()
    names: dict[UUID, str] = dict(
        db.execute(text("SELECT id, name FROM manufacturers WHERE tenant_id = :t"), {"t": tenant}).all()
    )
    for c in cases:
        manufacturer = names[c.manufacturer_id]
        r = dn.reminder_due(str(c.id), c.deadline_date, today)
        if r is None:
            continue
        for user_id in _recipients(db, tenant, c.owner_user_id):
            db.execute(
                insert(Notification)
                .values(
                    tenant_id=tenant,
                    id=new_id(),
                    user_id=user_id,
                    kind=dn.KIND,
                    entity_type="variance_case",
                    entity_id=c.id,
                    title=_title(
                        c.case_type,
                        c.variance_amount - c.recovered_amount,
                        manufacturer,
                        deadlines.days_remaining(c.deadline_date, today) or 0,
                    ),
                    body=f"Deadline {r.due.isoformat()}; reason {c.reason_code}.",
                    due_date=c.deadline_date,
                    dedupe_key=r.dedupe_key,
                    created_at=clock.now(),
                )
                .on_conflict_do_nothing(constraint="uq_notifications_dedupe")
            )
    for user_id in db.scalars(
        select(Notification.user_id)
        .where(
            Notification.tenant_id == tenant,
            Notification.emailed_at.is_(None),
            text(
                "notifications.user_id IN (SELECT user_id FROM memberships WHERE tenant_id = :t AND status = 'active')"
            ).bindparams(t=tenant),
        )
        .distinct()
        .order_by(Notification.user_id)
    ):
        enqueue(
            db,
            DIGEST_JOB,
            tenant_id=tenant,
            payload={"user_id": str(user_id)},
            dedupe_key=f"{DIGEST_JOB}:{tenant}:{user_id}:{today.isoformat()}",
        )
    _schedule_next(db, tenant, today)


def _schedule_next(db: Session, tenant: UUID, today: date) -> None:
    tomorrow = today + timedelta(days=1)
    tz = ZoneInfo(tenancy.get_settings(db, tenant).business_timezone)
    service.schedule_sweep(db, tenant, tomorrow, run_at=datetime.combine(tomorrow, SWEEP_AT, tz))


def _sweep_died(db: Session, ctx: JobContext) -> None:
    """A dead-lettered sweep must not end the chain (reminders would stop until a new case opens)."""
    tenant = _tenant(ctx)
    _schedule_next(db, tenant, service.business_today(db, tenant))


@job(DIGEST_JOB, tenant_scoped=True)
def recovery_digest(db: Session, ctx: JobContext) -> None:
    tenant = _tenant(ctx)
    user_id = UUID(ctx.payload["user_id"])
    unsent = db.scalars(
        select(Notification)
        .where(
            Notification.tenant_id == tenant,
            Notification.user_id == user_id,
            Notification.emailed_at.is_(None),
        )
        .order_by(Notification.due_date, Notification.id)
    ).all()
    to = db.scalar(  # only active members of this workspace get financial mail
        text(
            "SELECT u.email FROM users u JOIN memberships m ON m.user_id = u.id AND m.tenant_id = :t "
            "AND m.status = 'active' WHERE u.id = :u"
        ),
        {"t": tenant, "u": user_id},
    )
    if not unsent or to is None:
        return
    entries = [
        (
            str(user_id),
            dn.Reminder(
                str(n.entity_id), int(n.dedupe_key.rsplit(":", 1)[1]), n.due_date or date.min, n.dedupe_key
            ),
        )
        for n in unsent
    ]
    (digest,) = dn.group_digest(entries)
    by_key = {n.dedupe_key: n for n in unsent}
    lines = [f"- {by_key[r.dedupe_key].title} (deadline {r.due.isoformat()})" for r in digest.reminders]
    deliver(
        ctx.settings,
        Mail(
            "recovery_digest",
            to,
            f"Veyra Recover: {len(lines)} recovery deadline reminder(s)",
            "Recovery cases with approaching deadlines:\n\n" + "\n".join(lines) + "\n",
        ),
    )
    db.execute(
        update(Notification)
        .where(Notification.tenant_id == tenant, Notification.id.in_([n.id for n in unsent]))
        .values(emailed_at=clock.now())
    )
