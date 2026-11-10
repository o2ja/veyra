"""FastAPI app factory: request ids, error envelope, health routes, startup role self-check."""

import hmac
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from rebate.api import health, jobs_router
from rebate.api.deps import RequestContextDep, origin_ok
from rebate.api.paths import API_PREFIX, route_template
from rebate.core.config import Settings, get_settings
from rebate.core.db import assert_safe_role, make_engine, transaction
from rebate.core.errors import CsrfFailed, DomainError, RateLimited, Unauthenticated, error_body
from rebate.core.ids import new_id
from rebate.core.logging import configure_logging
from rebate.modules import audit, files, identity, ingestion, masterdata, tenancy
from rebate.modules.identity import ratelimit

log = structlog.get_logger("rebate.api")

# Every API router, in one place: the route-access meta-test walks exactly these.
MODULE_ROUTERS = (
    identity.router,
    tenancy.router,
    audit.router,
    files.router,
    jobs_router.router,
    masterdata.router,
    ingestion.router,
)
PUBLIC_ROUTERS = (health.router,)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


UPLOAD_PATH = f"{API_PREFIX}/files"
UPLOAD_OVERHEAD = 1024 * 1024  # multipart framing + the kind field


UPLOAD_USER_LIMIT = 30  # uploads per user per minute (pre-body gate)


def _error(exc: DomainError, request_id: str | None) -> JSONResponse:
    return JSONResponse(error_body(exc.code, exc.message, request_id, exc.details), exc.status_code)


def _upload_caller(request: Request) -> JSONResponse | None:
    """Before any body byte is read: a live session, Origin and session-bound CSRF token, and a
    per-user upload rate limit. Everything else (MFA state, permission, audited denial) is enforced
    by the route's dependencies as for every other request."""
    settings: Settings = request.app.state.settings
    rid = _request_id(request)
    token = request.cookies.get(identity.SESSION_COOKIE)
    sent = request.headers.get("x-csrf-token", "")
    if not origin_ok(request, settings) or not sent:
        return _error(CsrfFailed(), rid)
    if not token:
        return _error(Unauthenticated(), rid)
    with transaction(request.app.state.engine) as db:
        principal, _ = identity.resolve_principal(db, settings, token)
        if principal is None:
            return _error(Unauthenticated(), rid)
        if not hmac.compare_digest(sent, identity.csrf_for(settings, principal.session_id)):
            return _error(CsrfFailed(), rid)
        try:
            ratelimit.hit(db, f"upload:user:{principal.user_id}", limit=UPLOAD_USER_LIMIT, window_seconds=60)
        except RateLimited as exc:
            return _error(exc, rid)  # the counter increment still commits
    return None


async def _upload_gate(request: Request) -> JSONResponse | None:
    """Reject uploads before the body is read (SECURITY.md §4: reject early)."""
    if request.method != "POST" or request.url.path != UPLOAD_PATH:
        return None
    settings: Settings = request.app.state.settings
    if "transfer-encoding" in request.headers:  # chunked framing would override the checked length
        return JSONResponse(
            error_body("length_required", "Send the upload with a Content-Length.", _request_id(request)), 411
        )
    limit = (
        max(settings.upload_max_mb_spreadsheet, settings.upload_max_mb_pdf) * 1024 * 1024 + UPLOAD_OVERHEAD
    )
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        return JSONResponse(
            error_body("length_required", "Send the upload with a Content-Length.", None), 411
        )
    if int(length) > limit:
        return JSONResponse(error_body("payload_too_large", "The file is too large.", None), 413)
    return await run_in_threadpool(_upload_caller, request)


async def _request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request_id = str(new_id())  # never trust a client-supplied id
    request.state.request_id = request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    try:
        early = await _upload_gate(request)
        response = early if early is not None else await call_next(request)
    except Exception:
        log.exception("unhandled_error")
        response = JSONResponse(
            error_body("internal_error", "An unexpected error occurred.", request_id), 500
        )
    response.headers["X-Request-Id"] = request_id
    _security_headers(request, response)
    log.info(
        "request",
        method=request.method,
        # route template, never the raw path: paths may carry ids, and must never carry tokens
        route=route_template(request),
        status=response.status_code,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return response


def _security_headers(request: Request, response: Response) -> None:
    """API responses (API_SPECIFICATION.md §5, SECURITY.md §6). CSP/HSTS for the SPA are set by Caddy."""
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Cache-Control", "no-store")
    if not request.url.path.startswith(f"{API_PREFIX}/docs"):
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        body = error_body(exc.code, exc.message, _request_id(request), exc.details)
        return JSONResponse(body, exc.status_code, headers=exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details: list[dict[str, Any]] = [
            {
                "field": ".".join(str(p) for p in err["loc"][1:]),
                "code": err["type"],
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        body = error_body(
            "validation_error", "One or more fields are invalid.", _request_id(request), details
        )
        return JSONResponse(body, 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "bad_request")
        message = "Not found." if exc.status_code == 404 else "The request could not be processed."
        return JSONResponse(error_body(code, message, _request_id(request)), exc.status_code)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        assert_safe_role(engine)  # raises UnsafeDatabaseRole: the process does not start
        yield
        engine.dispose()

    expose_docs = settings.app_env != "production"  # unauthenticated docs only outside production
    app = FastAPI(
        title="Veyra Recover API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json" if expose_docs else None,
        docs_url=f"{API_PREFIX}/docs" if expose_docs else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.settings = settings
    # audit cannot import identity (identity -> audit); the API layer wires the lookup in
    app.state.actor_labels = identity.actor_labels
    app.middleware("http")(_request_context)
    _register_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    for router in MODULE_ROUTERS:
        app.include_router(router, prefix=API_PREFIX, dependencies=[RequestContextDep])
    return app
