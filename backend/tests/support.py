"""Shared test helpers: settings, a browser-like API client (cookies, Origin, CSRF), Mailpit reader.

Email delivery is checked through Mailpit's real HTTP API (S1 kickoff §5): a test does not pass
because a function was called, only because the message arrived at the SMTP server.
"""

import base64
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pyotp
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from rebate.api.app import create_app
from rebate.core import clock, jobs
from rebate.core.config import Settings

ORIGIN = "https://testserver"
TEST_KEYS = "k1:" + base64.b64encode(b"t" * 32).decode()
MAILPIT = os.environ.get("TEST_MAILPIT_URL", "http://mailpit:8025")
SMTP_HOST = os.environ.get("TEST_SMTP_HOST", "mailpit")
STRONG_PASSWORD = "correct horse battery staple 42"  # not in the common list, 31 chars


def make_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "ci",
        "secret_key": SecretStr("x" * 40),
        "database_url": SecretStr(database_url),
        "encryption_keys": SecretStr(TEST_KEYS),
        "public_base_url": ORIGIN,
        "smtp_host": SMTP_HOST,
        # real SeaweedFS and ClamAV (compose service names; CI overrides via env)
        "s3_endpoint_url": os.environ.get("S3_ENDPOINT_URL", "http://seaweedfs:8333"),
        "s3_public_endpoint_url": os.environ.get("S3_ENDPOINT_URL", "http://seaweedfs:8333"),
        "s3_bucket": os.environ.get("TEST_S3_BUCKET", "rebate-test"),
        "s3_access_key_id": SecretStr(os.environ.get("S3_ACCESS_KEY_ID", "")),
        "s3_secret_access_key": SecretStr(os.environ.get("S3_SECRET_ACCESS_KEY", "")),
        "clamd_host": os.environ.get("CLAMD_HOST", "clamav"),
        "clamd_port": int(os.environ.get("CLAMD_PORT", "3310")),
        # cheap Argon2 for speed; the timing test builds its own settings with production parameters
        "argon2_time_cost": 1,
        "argon2_memory_kib": 8,
        "argon2_parallelism": 1,
    }
    values.update(overrides)
    return Settings(**values)


def make_app(database_url: str, **overrides: Any) -> FastAPI:
    return create_app(make_settings(database_url, **overrides))


class Api:
    """TestClient that behaves like the SPA: same-origin Origin header and the CSRF double submit."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def csrf(self) -> str:
        token = self.client.cookies.get("__Host-csrf")
        if token is None:
            self.client.get("/api/v1/auth/csrf")
            token = self.client.cookies.get("__Host-csrf")
        assert token is not None
        return str(token)

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": self.csrf(), **(extra or {})}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        response: httpx.Response = self.client.get(f"/api/v1{path}", **kw)
        return response

    def post(self, path: str, json: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
        r: httpx.Response = self.client.post(f"/api/v1{path}", json=json, headers=self._headers(headers))
        return r

    def patch(self, path: str, json: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
        r: httpx.Response = self.client.patch(f"/api/v1{path}", json=json, headers=self._headers(headers))
        return r

    def delete(self, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
        r: httpx.Response = self.client.delete(f"/api/v1{path}", headers=self._headers(headers))
        return r


@contextmanager
def api_client(app: FastAPI, ip: str | None = None) -> Iterator[Api]:
    """Each client gets its own IP unless one is given, so per-IP rate limits do not couple tests."""
    peer = ip or f"10.{os.urandom(1)[0]}.{os.urandom(1)[0]}.{os.urandom(1)[0] or 1}"
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False, client=(peer, 50000)) as client:
        _APPS.append(app)
        try:
            yield Api(client)
        finally:
            _APPS.remove(app)


_APPS: list[FastAPI] = []


def drain(app: FastAPI | None = None, limit: int = 200) -> int:
    """Run due jobs in-process (the test's worker) until the queue is empty. Returns jobs run."""
    apps = [app] if app is not None else list(dict.fromkeys(_APPS))
    ran = 0
    for a in apps:
        while ran < limit and jobs.run_once(a.state.engine, a.state.settings):
            ran += 1
    return ran


# ---- Mailpit --------------------------------------------------------------------------------------

TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")


def messages_to(address: str, timeout: float = 10.0, expect: int = 1) -> list[dict[str, Any]]:
    """Wait until at least ``expect`` messages to ``address`` arrived; return them newest first."""
    drain()
    deadline = time.monotonic() + timeout
    while True:
        found = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'to:"{address}"'}, timeout=5).json()
        if len(found["messages"]) >= expect or time.monotonic() > deadline:
            return [
                httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=5).json() for m in found["messages"]
            ]
        time.sleep(0.1)


def latest_token(address: str, expect: int = 1) -> str:
    msgs = messages_to(address, expect=expect)
    assert msgs, f"no email arrived for {address}"
    match = TOKEN_RE.search(msgs[0]["Text"])
    assert match, f"no link in email {msgs[0]['Subject']!r}"
    return match.group(1)


class FakeClock:
    """Advancing replacement for ``core.clock`` (the app's only source of "now")."""

    active: FakeClock | None = None

    def __init__(self) -> None:
        self.t = datetime.now(UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@contextmanager
def fake_clock() -> Iterator[FakeClock]:
    fc = FakeClock()
    previous = clock._now
    clock._now = fc.now
    FakeClock.active = fc
    try:
        yield fc
    finally:
        clock._now = previous
        FakeClock.active = None


def totp_now(otpauth_uri: str) -> str:
    """Code for the next unused time step: advances the fake clock one step first, because the app
    rejects a second code from the same step (replay protection)."""
    if FakeClock.active is not None:
        FakeClock.active.advance(30)
    totp = pyotp.parse_uri(otpauth_uri)
    assert isinstance(totp, pyotp.TOTP)
    return totp.at(int(clock.now().timestamp()))


def unique_email(prefix: str) -> str:
    return f"{prefix}-{os.urandom(5).hex()}@example.test"
