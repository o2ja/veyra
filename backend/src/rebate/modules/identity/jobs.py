"""identity email jobs. Token decision (S2): the request stores the row with ``token_hash`` NULL and
enqueues the row id; the job mints the token, stores only its hash and sends the link, all in one
transaction. A raw token therefore never exists in a payload, a table or a log. A retry after an SMTP
failure mints a new token (the hash write rolled back with the failed attempt)."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, job
from rebate.core.mail import deliver
from rebate.core.request import RequestContext
from rebate.core.security import hash_token, new_token
from rebate.modules.audit import Action, Actor
from rebate.modules.identity import emails
from rebate.modules.identity.models import PasswordResetRequest, PendingRegistration, User, UserToken


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _job_context(db: Session, ctx: JobContext) -> RequestContext:
    """Audit helper context for writes made by a job (no client IP or user agent)."""
    return RequestContext(db=db, settings=ctx.settings, request_id=f"job:{ctx.id}", ip=None, user_agent=None)


@job("identity_registration_email", tenant_scoped=False)
def registration_email(db: Session, ctx: JobContext) -> None:
    pending = db.get(PendingRegistration, UUID(ctx.payload["pending_id"]), with_for_update=True)
    if pending is None or pending.used_at is not None or pending.expires_at <= clock.now():
        return  # nothing to send any more
    if _user_by_email(db, pending.email) is not None:
        deliver(ctx.settings, emails.already_registered(ctx.settings, pending.email))
        return
    token = new_token()
    pending.token_hash = hash_token(token)
    db.flush()
    deliver(ctx.settings, emails.verify_email(ctx.settings, pending.email, token))


def _drop_reset_request(db: Session, ctx: JobContext) -> None:
    """Dead letter: the typed address is not kept once no email will be sent."""
    request = db.get(PasswordResetRequest, UUID(ctx.payload["request_id"]))
    if request is not None:
        db.delete(request)


@job("identity_password_reset", tenant_scoped=False, on_dead=_drop_reset_request)
def password_reset(db: Session, ctx: JobContext) -> None:
    from rebate.modules.identity.service import audit_user_event  # service imports this module

    request = db.get(PasswordResetRequest, UUID(ctx.payload["request_id"]), with_for_update=True)
    if request is None:
        return
    email = request.email
    db.delete(request)
    user = _user_by_email(db, email)
    if user is None or user.status != "active":
        db.flush()
        return  # unknown address: the request looked identical, nothing is sent
    now = clock.now()
    db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user.id, UserToken.purpose == "password_reset", UserToken.used_at.is_(None)
        )
        .values(used_at=now)
    )
    token = new_token()
    db.add(
        UserToken(
            id=new_id(),
            user_id=user.id,
            purpose="password_reset",
            token_hash=hash_token(token),
            expires_at=now + timedelta(minutes=ctx.settings.password_reset_token_minutes),
        )
    )
    db.flush()
    audit_user_event(
        _job_context(db, ctx), user.id, Action.AUTH_PASSWORD_RESET_REQUESTED, actor=Actor("system")
    )
    deliver(ctx.settings, emails.password_reset(ctx.settings, user.email, token))


@job("identity_email_change", tenant_scoped=False)
def email_change(db: Session, ctx: JobContext) -> None:
    row = db.get(UserToken, UUID(ctx.payload["token_id"]), with_for_update=True)
    if row is None or row.purpose != "email_change" or row.used_at is not None or row.new_email is None:
        return
    if row.expires_at <= clock.now():
        return
    user = db.get(User, row.user_id)
    if user is None:
        return
    if _user_by_email(db, row.new_email) is not None:
        deliver(ctx.settings, emails.email_change_in_use(ctx.settings, row.new_email))
    else:
        token = new_token()
        row.token_hash = hash_token(token)
        db.flush()
        deliver(ctx.settings, emails.email_change_confirm(ctx.settings, row.new_email, token))
    deliver(ctx.settings, emails.email_change_notice(ctx.settings, user.email))
