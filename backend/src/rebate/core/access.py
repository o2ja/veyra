"""Route access declarations (API_SPECIFICATION.md §1.4, SECURITY.md §6).

Every route declares exactly one of:

* ``Depends(public())`` — no session needed (register, login, reset, verify, accept, health, docs);
* ``Depends(authenticated(...))`` — any signed-in user, no workspace permission (me, logout, MFA setup);
* ``Depends(require("perm"))`` — a permission from the registry in ``tenancy.policies``.

``tests/api/test_route_access.py`` fails if a route declares none or more than one. Denials of a
permission raise ``PermissionDenied`` which the API layer audits (rate-limited) after rollback.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import Request

from rebate.core import clock
from rebate.core.errors import (
    Forbidden,
    MfaEnrollmentRequired,
    MfaRequired,
    ReauthenticationRequired,
    SessionExpired,
    Unauthenticated,
)
from rebate.core.request import Principal, ctx

ACCESS_ATTR = "__route_access__"


class PermissionDenied(Forbidden):
    def __init__(self, permission: str) -> None:
        super().__init__()
        self.permission = permission


def _mark(fn: Callable[..., Any], kind: str, value: str | None = None) -> Callable[..., Any]:
    setattr(fn, ACCESS_ATTR, (kind, value))
    return fn


def public() -> Callable[[], None]:
    def dependency() -> None:
        return None

    return _mark(dependency, "public")


def _signed_in(request: Request) -> Principal:
    context = ctx(request)
    if context.principal is None:
        raise SessionExpired() if context.session_expired else Unauthenticated()
    return context.principal


def authenticated(
    *, allow_mfa_pending: bool = False, allow_enrollment: bool = False
) -> Callable[..., Principal]:
    """Signed in. By default MFA must be satisfied and no forced enrolment may be pending."""

    def dependency(request: Request) -> Principal:
        principal = _signed_in(request)
        if principal.mfa_pending and not allow_mfa_pending:
            raise MfaRequired()
        if not principal.mfa_pending and principal.enrollment_required and not allow_enrollment:
            raise MfaEnrollmentRequired()
        return principal

    kind = "authenticated"
    if allow_mfa_pending:
        kind += ":mfa_pending"
    if allow_enrollment:
        kind += ":enrollment"
    return _mark(dependency, kind)


def require(permission: str) -> Callable[..., Principal]:
    """Signed in, MFA satisfied, active workspace, and the role grants ``permission``."""

    def dependency(request: Request) -> Principal:
        principal = _signed_in(request)
        if principal.mfa_pending:
            raise MfaRequired()
        if principal.enrollment_required:
            raise MfaEnrollmentRequired()
        if principal.tenant_id is None or permission not in principal.permissions:
            raise PermissionDenied(permission)
        return principal

    return _mark(dependency, "permission", permission)


def tenant_of(principal: Principal) -> UUID:
    """The active workspace of a principal that passed ``require()``."""
    if principal.tenant_id is None:
        raise PermissionDenied("tenant")
    return principal.tenant_id


def require_recent_reauth(principal: Principal, window_minutes: int) -> None:
    """Step-up (SECURITY.md §1): password (+ MFA) re-entered within the window."""
    at = principal.reauthenticated_at
    if at is None or clock.now() - at > timedelta(minutes=window_minutes):
        raise ReauthenticationRequired()
