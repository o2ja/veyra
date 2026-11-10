"""tenancy jobs: invitation email (token minted here, S2 token decision; see identity/jobs.py)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.jobs import JobContext, PermanentError, job
from rebate.core.mail import Mail, deliver
from rebate.core.security import hash_token, new_token
from rebate.modules.tenancy.models import Invitation, Tenant


@job("tenancy_invitation_email", tenant_scoped=True)
def invitation_email(db: Session, ctx: JobContext) -> None:
    inv = db.scalar(
        select(Invitation)
        .where(Invitation.tenant_id == ctx.tenant_id, Invitation.id == UUID(ctx.payload["invitation_id"]))
        .with_for_update()
    )
    if inv is None:  # RLS + explicit tenant filter: an id from another tenant is simply not found
        raise PermanentError("invitation not found in this workspace")
    if inv.accepted_at is not None or inv.revoked_at is not None or inv.expires_at <= clock.now():
        return
    workspace = db.scalar(select(Tenant.name).where(Tenant.id == ctx.tenant_id)) or "Veyra Recover"
    token = new_token()
    inv.token_hash = hash_token(token)
    db.flush()
    days = ctx.settings.invitation_days
    link = f"{ctx.settings.public_origin}/accept-invite#token={token}"  # fragment: never sent to a server
    deliver(
        ctx.settings,
        Mail(
            template="invitation",
            to=inv.email,
            subject=f"You're invited to {workspace} on Veyra Recover",
            body=(
                f'You have been invited to join the workspace "{workspace}" on Veyra Recover.\n\n'
                f"Accept the invitation: {link}\n\n"
                f"The link works once and expires within {days} days. "
                "If you did not expect this, ignore this email."
            ),
        ),
    )
