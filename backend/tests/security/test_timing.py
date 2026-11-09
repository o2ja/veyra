"""Uniform timing for existing vs unknown emails (SECURITY.md §1, §11.8; S1 review finding 11).

Uses the production Argon2 parameters (t=3, 64 MiB, p=4). Known users have three workspace
memberships, so the per-membership audit writes (D-7) are included in what is measured. Samples are
interleaved (known, unknown, known, ...) so machine load affects both groups alike.

Tolerance: medians within 25 %. This is a statistical test; a failure means "measure again and look",
and the tolerance must not be loosened silently (CLAUDE.md §14).
"""

import statistics
import time
from collections.abc import Callable
from functools import partial

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.core.ids import new_id
from tests.conftest import DbUrls
from tests.support import STRONG_PASSWORD, Api, api_client, make_app, unique_email

SAMPLES = 15
TOLERANCE = 0.25


@pytest.fixture(scope="module")
def prod_app(migrated_db: DbUrls) -> FastAPI:
    return make_app(migrated_db.app, argon2_time_cost=3, argon2_memory_kib=65536, argon2_parallelism=4)


@pytest.fixture(scope="module")
def known_emails(admin_engine: Engine, prod_app: FastAPI) -> list[str]:
    """SAMPLES verified users, each a member of three workspaces (seeded directly; tests only)."""
    from rebate.modules.identity.service import password_hasher

    hashed = password_hasher(prod_app.state.settings).hash(STRONG_PASSWORD)
    emails = []
    with admin_engine.begin() as conn:
        tenants = []
        for i in range(3):
            tid = new_id()
            conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                {"id": tid, "n": f"Timing {i} (SYNTHETIC)", "s": f"timing-{tid.hex[-12:]}"},
            )
            conn.execute(text("INSERT INTO tenant_settings (tenant_id) VALUES (:t)"), {"t": tid})
            tenants.append(tid)
        for _ in range(SAMPLES):
            uid, email = new_id(), unique_email("timing")
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, full_name, email_verified_at) "
                    "VALUES (:id, :e, :h, 'T', now())"
                ),
                {"id": uid, "e": email, "h": hashed},
            )
            for tid in tenants:
                conn.execute(
                    text(
                        "INSERT INTO memberships (tenant_id, id, user_id, role) "
                        "VALUES (:t, :id, :u, 'viewer')"
                    ),
                    {"t": tid, "id": new_id(), "u": uid},
                )
            emails.append(email)
    return emails


def _measure(app: FastAPI, call: Callable[[Api], object]) -> float:
    with api_client(app) as api:  # fresh IP: per-IP limits never short-circuit a sample
        api.csrf()
        start = time.perf_counter()
        call(api)
        return time.perf_counter() - start


def _at(fn: Callable[[Api, int], object], i: int, api: Api) -> object:
    return fn(api, i)


def _compare(
    app: FastAPI, known: Callable[[Api, int], object], unknown: Callable[[Api, int], object]
) -> None:
    k: list[float] = []
    u: list[float] = []
    for i in range(SAMPLES):
        k.append(_measure(app, partial(_at, known, i)))
        u.append(_measure(app, partial(_at, unknown, i)))
    mk, mu = statistics.median(k), statistics.median(u)
    ratio = abs(mk - mu) / max(mk, mu)
    assert ratio < TOLERANCE, f"median known {mk * 1000:.1f} ms vs unknown {mu * 1000:.1f} ms ({ratio:.0%})"


def test_login_timing(prod_app: FastAPI, known_emails: list[str]) -> None:
    _compare(
        prod_app,
        lambda api, i: api.post("/auth/login", json={"email": known_emails[i], "password": "wrong wrong 1"}),
        lambda api, i: api.post(
            "/auth/login", json={"email": unique_email("t"), "password": "wrong wrong 1"}
        ),
    )


def test_register_timing(prod_app: FastAPI, known_emails: list[str]) -> None:
    def register(api: Api, email: str) -> object:
        return api.post(
            "/auth/register",
            json={"email": email, "full_name": "T", "workspace_name": "T", "password": STRONG_PASSWORD},
        )

    _compare(
        prod_app,
        lambda api, i: register(api, known_emails[i]),
        lambda api, i: register(api, unique_email("t")),
    )


def test_forgot_password_timing(prod_app: FastAPI, known_emails: list[str]) -> None:
    _compare(
        prod_app,
        lambda api, i: api.post("/auth/password/forgot", json={"email": known_emails[i]}),
        lambda api, i: api.post("/auth/password/forgot", json={"email": unique_email("t")}),
    )
