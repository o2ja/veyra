"""Expiry sweep (S4.6, FR-AG-10/12): a tenant-scoped job that runs once per business day.

* active versions whose ``valid_to`` is before the business date become ``expired`` (audited);
* versions entering the alert window (``valid_to`` within ``ALERT_DAYS``) since the previous run get an
  ``agreement_version.expiry_alert`` audit entry: the notification hook. S9 (recovery notifications) turns it
  into user notifications;
* the job enqueues tomorrow's run (dedupe key per tenant and day, so approvals never start a second chain).
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core.jobs import JobContext, PermanentError, job
from rebate.modules.agreements import service
from rebate.modules.agreements.models import AgreementVersion
from rebate.modules.audit import Action
from rebate.modules.masterdata import Who

ALERT_DAYS = 30


@job(service.SWEEP_JOB, tenant_scoped=True)
def expiry_sweep(db: Session, ctx: JobContext) -> None:
    tenant = ctx.tenant_id
    if tenant is None:  # unreachable: the queue refuses a tenant-scoped job without a tenant
        raise PermanentError("tenant-scoped job without tenant context")
    who = Who(tenant, tenant, {})  # system actor: user_id is not recorded
    today = service.business_today(db, tenant)
    since = date.fromisoformat(ctx.payload["since"]) if ctx.payload.get("since") else None
    for v in db.scalars(
        select(AgreementVersion)
        .where(AgreementVersion.tenant_id == tenant, AgreementVersion.status == "active")
        .order_by(AgreementVersion.id)
        .with_for_update()
    ).all():
        if v.valid_to < today:
            before = service.version_audit(v)
            v.status = "expired"
            db.flush()
            service.audit_event(
                db,
                who,
                Action.AGREEMENT_VERSION_EXPIRED,
                "agreement_version",
                v.id,
                system=True,
                before=before,
                after=service.version_audit(v),
            )
            continue
        window_start = v.valid_to - timedelta(days=ALERT_DAYS)
        if window_start <= today and (since is None or window_start > since):
            service.audit_event(
                db,
                who,
                Action.AGREEMENT_VERSION_EXPIRY_ALERT,
                "agreement_version",
                v.id,
                system=True,
                after={**service.version_audit(v), "days_left": (v.valid_to - today).days},
            )
    service.schedule_sweep(db, tenant, today + timedelta(days=1), since=today)
