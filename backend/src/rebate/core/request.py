"""Per-request context shared by the API layer and module routers.

The API layer (``rebate.api.deps.request_context``) opens the request transaction, resolves the
session, sets ``app.tenant_id`` and stores a ``RequestContext`` on ``request.state``. Module routers
read it through ``ctx()``; they never open their own transaction and never see a tenant id from the
client (SECURITY.md §2).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from rebate.core.config import Settings


@dataclass(frozen=True)
class Principal:
    """Who is calling. ``tenant_id`` is None for a session without an active workspace."""

    session_id: str  # SHA-256 of the opaque token (what the DB stores)
    user_id: UUID
    email: str
    tenant_id: UUID | None
    role: str | None
    permissions: frozenset[str]
    mfa_satisfied: bool  # MFA verified, or neither the user nor the workspace requires it
    mfa_pending: bool  # password ok, TOTP still to enter (pre-MFA session)
    enrollment_required: bool  # workspace requires MFA, user has not enrolled
    user_mfa_enabled: bool
    reauthenticated_at: datetime | None


@dataclass
class RequestContext:
    db: Session
    settings: Settings
    request_id: str
    ip: str | None
    user_agent: str | None
    principal: Principal | None = None
    session_expired: bool = False  # a cookie was presented but the session is expired/revoked

    def audit_meta(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "ip": self.ip, "user_agent": self.user_agent}


def ctx(request: Request) -> RequestContext:
    context: RequestContext | None = getattr(request.state, "ctx", None)
    if context is None:  # a route was mounted without the API layer's request_context dependency
        raise RuntimeError("request context missing")
    return context
