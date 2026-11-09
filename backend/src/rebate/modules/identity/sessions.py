"""Server-side sessions (SECURITY.md §1, ADR-007).

The browser holds an opaque 256-bit token in ``__Host-session``; the DB stores only its SHA-256.
The CSRF token is HMAC(secret, session id), so it is bound to the session and cannot be planted.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import Response
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.config import Settings
from rebate.core.security import hash_token, new_token
from rebate.modules.identity.models import AuthSession

SESSION_COOKIE = "__Host-session"
CSRF_COOKIE = "__Host-csrf"


def csrf_for(settings: Settings, session_id: str) -> str:
    return hmac.new(
        settings.secret_key.get_secret_value().encode(), f"csrf\x00{session_id}".encode(), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class NewSession:
    token: str  # raw, only ever placed in the cookie
    row: AuthSession


def create(
    db: Session,
    settings: Settings,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    mfa_verified: bool,
    pending_mfa: bool,
    ip: str | None,
    user_agent: str | None,
    reauthenticated: bool = False,
) -> NewSession:
    now = clock.now()
    if pending_mfa:
        idle = absolute = now + timedelta(minutes=settings.pending_mfa_session_minutes)
    else:
        idle = now + timedelta(minutes=settings.session_idle_minutes)
        absolute = now + timedelta(hours=settings.session_absolute_hours)
    token = new_token()
    row = AuthSession(
        id=hash_token(token),
        user_id=user_id,
        active_tenant_id=tenant_id,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=min(idle, absolute),
        absolute_expires_at=absolute,
        mfa_verified=mfa_verified,
        reauthenticated_at=now if reauthenticated else None,
        mfa_failed_count=0,
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(row)
    db.flush()
    return NewSession(token=token, row=row)


def get_valid(db: Session, session_id: str) -> tuple[AuthSession | None, bool]:
    """Returns (session, expired_flag). Revoked or timed-out sessions are never returned."""
    row = db.scalar(select(AuthSession).where(AuthSession.id == session_id).with_for_update())
    if row is None:
        return None, False
    now = clock.now()
    if row.revoked_at is not None or now >= row.idle_expires_at or now >= row.absolute_expires_at:
        return None, True
    return row, False


def touch(row: AuthSession, settings: Settings, *, pending_mfa: bool) -> None:
    """Slide the idle timeout, writing at most once per ``session_touch_seconds``."""
    now = clock.now()
    if pending_mfa:
        return  # pre-MFA sessions have a short fixed lifetime and never slide
    if (now - row.last_seen_at).total_seconds() < settings.session_touch_seconds:
        return
    row.last_seen_at = now
    row.idle_expires_at = min(now + timedelta(minutes=settings.session_idle_minutes), row.absolute_expires_at)


def revoke(db: Session, session_id: str) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )


def revoke_all(db: Session, user_id: UUID, *, except_id: str | None = None) -> int:
    stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_id is not None:
        stmt = stmt.where(AuthSession.id != except_id)
    return cast(CursorResult[Any], db.execute(stmt.values(revoked_at=clock.now()))).rowcount


def revoke_in_tenant(db: Session, user_id: UUID, tenant_id: UUID) -> int:
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.active_tenant_id == tenant_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=clock.now())
    )
    return cast(CursorResult[Any], result).rowcount


def last_tenant(db: Session, user_id: UUID) -> UUID | None:
    return db.scalar(
        select(AuthSession.active_tenant_id)
        .where(AuthSession.user_id == user_id, AuthSession.active_tenant_id.is_not(None))
        .order_by(AuthSession.created_at.desc())
        .limit(1)
    )


def set_cookies(response: Response, settings: Settings, new: NewSession) -> None:
    max_age = int((new.row.absolute_expires_at - clock.now()).total_seconds())
    response.set_cookie(
        SESSION_COOKIE, new.token, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_for(settings, new.row.id),
        max_age=max_age,
        path="/",
        secure=True,
        httponly=False,
        samesite="strict",
    )


def clear_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=True, httponly=name == SESSION_COOKIE, samesite="lax")


def expires_at(row: AuthSession) -> datetime:
    return min(row.idle_expires_at, row.absolute_expires_at)
