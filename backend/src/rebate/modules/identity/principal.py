"""Resolve the session cookie into a ``Principal`` for one request (called by the API layer).

Fail closed: an expired/revoked session, a disabled user, or a membership that is no longer active
yields no principal (or no tenant). A tenant whose status is not ``active`` raises ``TenantSuspended``
(403) on every request, whatever the session age. Role and permissions are read from the DB on every request
(SECURITY.md §11.13: no stale roles).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core.config import Settings
from rebate.core.db import set_tenant
from rebate.core.errors import TenantSuspended
from rebate.core.request import Principal
from rebate.core.security import hash_token
from rebate.modules import tenancy
from rebate.modules.identity import sessions
from rebate.modules.identity.models import User


def resolve(db: Session, settings: Settings, token: str) -> tuple[Principal | None, bool]:
    """Returns (principal, expired). Sets ``app.tenant_id`` for the rest of the transaction."""
    session_id = hash_token(token)
    row, expired = sessions.get_valid(db, session_id)
    if row is None:
        return None, expired
    user = db.scalar(select(User).where(User.id == row.user_id))
    if user is None or user.status != "active":
        return None, True
    mfa_pending = user.mfa_enabled and not row.mfa_verified
    tenant_id, role, mfa_required = None, None, False
    if row.active_tenant_id is not None:
        set_tenant(db, row.active_tenant_id)
        if not tenancy.is_active(db, row.active_tenant_id):
            raise TenantSuspended()
        membership = tenancy.membership(db, row.active_tenant_id, user.id)
        if membership is not None and membership.status == "active":
            tenant_id, role = row.active_tenant_id, membership.role
            mfa_required = tenancy.get_settings(db, tenant_id).mfa_required
        else:
            set_tenant(db, None)
    enrollment_required = mfa_required and not user.mfa_enabled
    sessions.touch(row, settings, pending_mfa=mfa_pending)
    return (
        Principal(
            session_id=session_id,
            user_id=user.id,
            email=user.email,
            tenant_id=tenant_id,
            role=role,
            permissions=tenancy.permissions_for(role) if role else frozenset(),
            mfa_satisfied=not mfa_pending and not enrollment_required,
            mfa_pending=mfa_pending,
            enrollment_required=enrollment_required,
            user_mfa_enabled=user.mfa_enabled,
            reauthenticated_at=row.reauthenticated_at,
        ),
        False,
    )
