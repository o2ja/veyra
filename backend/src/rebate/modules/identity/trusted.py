"""Trusted devices: a 30-day HttpOnly cookie that lets a known browser skip the 2FA prompt.

Only the SHA-256 of the random token is stored. Rows are deleted on password change/reset, 2FA
(re-)enrolment and "forget trusted devices".
"""

from datetime import timedelta
from uuid import UUID

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.ids import new_id
from rebate.core.security import hash_token, new_token
from rebate.modules.identity.models import TrustedDevice

COOKIE = "__Host-trusted-device"
DAYS = 30


def issue(db: Session, response: Response, user_id: UUID, tenant_id: UUID | None) -> None:
    token = new_token()
    now = clock.now()
    db.add(
        TrustedDevice(
            id=new_id(),
            user_id=user_id,
            tenant_id=tenant_id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=now + timedelta(days=DAYS),
        )
    )
    response.set_cookie(
        COOKIE, token, max_age=DAYS * 86400, path="/", secure=True, httponly=True, samesite="lax"
    )


def is_trusted(db: Session, token: str | None, user_id: UUID, tenant_id: UUID | None) -> bool:
    if not token:
        return False
    row = db.scalar(select(TrustedDevice).where(TrustedDevice.token_hash == hash_token(token)))
    return (
        row is not None
        and row.user_id == user_id
        and row.tenant_id == tenant_id
        and row.expires_at > clock.now()
    )


def revoke_all(db: Session, user_id: UUID) -> None:
    db.execute(delete(TrustedDevice).where(TrustedDevice.user_id == user_id))


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
