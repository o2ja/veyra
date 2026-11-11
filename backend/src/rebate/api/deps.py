"""The request pipeline (ARCHITECTURE.md §4, SECURITY.md §1-§2). One request = one transaction.

``request_context`` runs before every module route (router-level dependency, scope "function" so the
commit happens **before** the response is sent: a failed commit can never look like success):

1. open the transaction; 2. resolve the session cookie → Principal, set ``app.tenant_id`` (SET LOCAL);
3. enforce Origin + CSRF on mutating requests; 4. general rate limits; 5. yield to the route;
6. commit (or roll back; ``DomainError.commit`` failures commit their bookkeeping);
7. permission denials are audited in a separate, rate-limited
transaction after the rollback.
"""

import hmac
from collections.abc import Iterator
from urllib.parse import urlsplit

import structlog
from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from rebate.api.paths import route_template
from rebate.core.access import PermissionDenied
from rebate.core.clientip import client_ip
from rebate.core.config import Settings
from rebate.core.db import set_tenant
from rebate.core.errors import CsrfFailed, DomainError, RateLimited
from rebate.core.request import RequestContext
from rebate.modules import audit, identity
from rebate.modules.audit import Action, Actor
from rebate.modules.identity import ratelimit

log = structlog.get_logger("rebate.api")

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
GENERAL_USER_LIMIT = 600  # per minute (API_SPECIFICATION.md §1)
GENERAL_IP_LIMIT = 120  # per minute, unauthenticated


def origin_ok(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer is None:
            return False
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    return origin == settings.public_origin


def _check_csrf(request: Request, c: RequestContext) -> None:
    if request.method not in UNSAFE:
        return
    if not origin_ok(request, c.settings):
        raise CsrfFailed()
    sent = request.headers.get("x-csrf-token", "")
    if c.principal is not None:
        expected = identity.csrf_for(c.settings, c.principal.session_id)
    else:
        expected = request.cookies.get(identity.CSRF_COOKIE, "")  # pre-session double submit
    if not expected or not sent or not hmac.compare_digest(sent, expected):
        raise CsrfFailed()


def _general_limits(c: RequestContext) -> None:
    if c.principal is not None:
        ratelimit.hit(c.db, f"req:user:{c.principal.user_id}", limit=GENERAL_USER_LIMIT, window_seconds=60)
    elif c.ip is not None:
        ratelimit.hit(c.db, f"req:ip:{c.ip}", limit=GENERAL_IP_LIMIT, window_seconds=60)


AI_ROUTES = frozenset({"/api/v1/document-extractions", "/api/v1/imports/{import_id}/suggest-mapping"})


def _ai_limits(request: Request, c: RequestContext) -> None:
    """Per-workspace rate limit on the routes that can spend money at the AI provider (AI_PROCESSING.md §9)."""
    p = c.principal
    if (
        request.method == "POST"
        and p is not None
        and p.tenant_id is not None
        and route_template(request) in AI_ROUTES
    ):
        ratelimit.hit(
            c.db,
            f"ai:tenant:{p.tenant_id}:{route_template(request)}",
            limit=c.settings.ai_requests_per_minute,
            window_seconds=60,
        )


def _audit_denial(engine: Engine, c: RequestContext, request: Request, exc: PermissionDenied) -> None:
    """Separate transaction (the request's was rolled back). At most one entry per user x permission
    per minute (SECURITY.md §7: permission denials, rate-limited)."""
    p = c.principal
    if p is None or p.tenant_id is None:
        return
    with Session(engine) as db, db.begin():
        set_tenant(db, p.tenant_id)
        try:
            ratelimit.hit(db, f"deny:{p.user_id}:{exc.permission}", limit=1, window_seconds=60)
        except RateLimited:
            return
        audit.record(
            db,
            tenant_id=p.tenant_id,
            actor=Actor("user", p.user_id),
            action=Action.AUTHZ_DENIED,
            entity_type="permission",
            after={
                "permission": exc.permission,
                "method": request.method,
                "route": route_template(request),
            },
            meta=c.audit_meta(),
        )


def request_context(request: Request) -> Iterator[RequestContext]:
    settings: Settings = request.app.state.settings
    engine: Engine = request.app.state.engine
    session = Session(engine)
    session.begin()
    peer = request.client.host if request.client else None
    c = RequestContext(
        db=session,
        settings=settings,
        request_id=getattr(request.state, "request_id", ""),
        ip=client_ip(peer, request.headers.get("x-forwarded-for"), settings.trusted_proxy_ips),
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
    )
    request.state.ctx = c
    try:
        token = request.cookies.get(identity.SESSION_COOKIE)
        if token:
            c.principal, c.session_expired = identity.resolve_principal(session, settings, token)
        if c.principal is not None:
            structlog.contextvars.bind_contextvars(tenant_id=str(c.principal.tenant_id or ""))
        _check_csrf(request, c)
        _general_limits(c)
        _ai_limits(request, c)
        yield c
    except DomainError as exc:
        if exc.commit:
            session.commit()
        else:
            session.rollback()
            if isinstance(exc, PermissionDenied):
                _audit_denial(engine, c, request, exc)
        raise
    except BaseException:
        session.rollback()
        raise
    else:
        session.commit()  # session-level: a handler may have committed early (suggest-mapping)
    finally:
        session.close()


RequestContextDep = Depends(request_context, scope="function")
