"""Global request-body cap (review SEC-M): every route except file upload refuses bodies above
``body_max_mb`` with 413, whether the size is declared (Content-Length) or not (chunked)."""

from collections.abc import Iterator

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
        small = api.client.post("/api/v1/auth/login", content=_chunks(1024), headers=headers)
        # the replayed body reached validation: the email is malformed, not the body
        assert small.status_code in (401, 422), small.text
        assert small.json()["error"]["code"] != "payload_too_large"
