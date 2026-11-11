"""Global request-body cap (review SEC-M): every route except file upload refuses bodies above
``body_max_mb`` with 413, whether the size is declared (Content-Length) or not (chunked)."""

import asyncio
from collections.abc import Iterator, MutableMapping
from typing import Any

from fastapi import FastAPI

from tests.support import ORIGIN, api_client, make_app

CAP_MB = 1


def _app(database_url: str) -> FastAPI:
    return make_app(database_url, body_max_mb=CAP_MB)


def _chunks(total: int, size: int = 64 * 1024) -> Iterator[bytes]:
    yield b'{"email": "'
    sent = 0
    while sent < total:
        yield b"a" * size
        sent += size
    yield b'@example.test", "password": "x"}'


def test_declared_oversized_body_is_refused_before_parsing(migrated_db) -> None:  # type: ignore[no-untyped-def]
    with api_client(_app(migrated_db.app)) as api:
        body = b"{" + b" " * (CAP_MB * 1024 * 1024) + b"}"
        r = api.client.post("/api/v1/agreement-drafts", content=body,
                            headers={"Content-Type": "application/json", "Origin": ORIGIN})  # fmt: skip
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_chunked_body_over_the_cap_is_refused_and_a_small_one_reaches_the_route(migrated_db) -> None:  # type: ignore[no-untyped-def]
    with api_client(_app(migrated_db.app)) as api:
        headers = {"Content-Type": "application/json", "Origin": ORIGIN, "X-CSRF-Token": api.csrf()}
        big = api.client.post(
            "/api/v1/auth/login", content=_chunks(2 * CAP_MB * 1024 * 1024), headers=headers
        )
        assert big.status_code == 413 and big.json()["error"]["code"] == "payload_too_large"
        login = b'{"email": "nobody@example.test", "password": "wrong password 123"}'
        small = api.client.post("/api/v1/auth/login", content=iter([login[:20], login[20:]]), headers=headers)
        # 401 (not 422): the route parsed the replayed body intact and checked the credentials
        assert small.status_code == 401, small.text


def test_content_length_together_with_chunked_is_refused(migrated_db) -> None:  # type: ignore[no-untyped-def]
    """Review SEC-M bypass: h11 keeps both headers and frames the body as chunked, so a small declared
    Content-Length must not let a large chunked body through. Raw ASGI: httpx never sends both headers."""
    app = _app(migrated_db.app)
    sent: list[MutableMapping[str, Any]] = []
    body = [b"{" + b" " * (2 * CAP_MB * 1024 * 1024) + b"}"]

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body.pop() if body else b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "https", "path": "/api/v1/auth/login", "raw_path": b"/api/v1/auth/login",
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1), "server": ("testserver", 443),
             "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                         (b"content-length", b"1"), (b"transfer-encoding", b"chunked")]}  # fmt: skip
    asyncio.run(app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 400, sent[0]
