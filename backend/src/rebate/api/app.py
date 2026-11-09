"""FastAPI app factory: request ids, error envelope, health routes, startup role self-check."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rebate.api import health
from rebate.core.config import Settings, get_settings
from rebate.core.db import assert_safe_role, make_engine
from rebate.core.errors import DomainError, error_body
from rebate.core.ids import new_id
from rebate.core.logging import configure_logging

log = structlog.get_logger("rebate.api")

API_PREFIX = "/api/v1"
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request_id = str(new_id())  # never trust a client-supplied id
    request.state.request_id = request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled_error")
        response = JSONResponse(
            error_body("internal_error", "An unexpected error occurred.", request_id), 500
        )
    response.headers["X-Request-Id"] = request_id
    log.info(
        "request",
        method=request.method,
        path=request.url.path,  # path only: no query strings in logs
        status=response.status_code,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return response


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        body = error_body(exc.code, exc.message, _request_id(request), exc.details)
        return JSONResponse(body, exc.status_code)

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
    app.middleware("http")(_request_context)
    _register_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    return app
