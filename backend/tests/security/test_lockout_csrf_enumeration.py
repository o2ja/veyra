"""Lockout, no account enumeration, CSRF/Origin, rate limits, client IP (SECURITY.md §1, §6; kickoff
review findings 3, 4, 17, 18)."""

from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.core.clientip import client_ip
from tests.security.flows import full_admin, register_and_verify
from tests.support import ORIGIN, STRONG_PASSWORD, Api, FakeClock, api_client, totp_now, unique_email


def _login(api: Api, email: str, password: str) -> tuple[int, dict[str, Any]]:
    r = api.post("/auth/login", json={"email": email, "password": password})
    return r.status_code, r.json()


def _fresh_ip_logins(app: FastAPI, email: str, password: str, n: int) -> list[tuple[int, dict[str, Any]]]:
    """n attempts, each from a new IP so the 5/min per IP+account limit does not mask the lockout."""
    out = []
    for _ in range(n):
        with api_client(app) as api:
            out.append(_login(api, email, password))
    return out


def _codes(results: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, str]]:
    return [(status, body["error"]["code"]) for status, body in results]


def test_five_failures_lock_the_account_and_audit_survives(
    app: FastAPI, api: Api, clk: FakeClock, admin_engine: Engine
) -> None:
    account = full_admin(api, unique_email("lock"))
    results = _codes(_fresh_ip_logins(app, account.email, "wrong password number 1", 6))
    assert results[:4] == [(401, "unauthenticated")] * 4
    assert results[4:] == [(423, "account_locked")] * 2
    # the correct password is refused while locked (and the answer is the same)
    assert _codes(_fresh_ip_logins(app, account.email, account.password, 1)) == [(423, "account_locked")]
    with admin_engine.connect() as conn:
        actions = conn.execute(
            text(
                "SELECT a.action, count(*) FROM audit_log a JOIN users u ON u.id = a.entity_id "
                "WHERE u.email = :e GROUP BY a.action"
            ),
            {"e": account.email},
        ).all()
    counts: dict[str, int] = {str(k): int(v) for k, v in actions}
    assert counts["auth.login_failed"] >= 5 and counts["auth.locked"] == 1  # committed despite the errors
    clk.advance(16 * 60)
    with api_client(app) as fresh:
        status, body = _login(fresh, account.email, account.password)
    assert status == 200 and body["status"] == "mfa_required"


def test_lockout_is_progressive_and_decays(app: FastAPI, api: Api, clk: FakeClock) -> None:
    account = register_and_verify(api, unique_email("progressive"))
    _fresh_ip_logins(app, account.email, "bad bad bad bad 1", 5)  # lock 1: 15 min
    clk.advance(16 * 60)
    _fresh_ip_logins(app, account.email, "bad bad bad bad 2", 5)  # lock 2: 30 min
    clk.advance(16 * 60)
    assert _codes(_fresh_ip_logins(app, account.email, account.password, 1)) == [(423, "account_locked")]
    clk.advance(15 * 60)
    status, _ = _fresh_ip_logins(app, account.email, account.password, 1)[0]
    assert status == 200
    clk.advance(25 * 3600)  # level decays after 24 h without lockout
    _fresh_ip_logins(app, account.email, "bad bad bad bad 3", 5)
    clk.advance(16 * 60)
    assert _fresh_ip_logins(app, account.email, account.password, 1)[0][0] == 200  # back to 15 min


def test_unknown_email_behaves_exactly_like_a_real_account(app: FastAPI, api: Api) -> None:
    real = register_and_verify(api, unique_email("realacct"))
    ghost = unique_email("ghost")
    real_results = _codes(_fresh_ip_logins(app, real.email, "wrong password number 9", 6))
    ghost_results = _codes(_fresh_ip_logins(app, ghost, "wrong password number 9", 6))
    assert real_results == ghost_results  # same codes in the same order, including the 423s
    with api_client(app) as a, api_client(app) as b:
        ra = a.post("/auth/login", json={"email": unique_email("x"), "password": "whatever whatever"})
        rb = b.post("/auth/login", json={"email": real.email + "x", "password": "whatever whatever"})
    assert ra.json()["error"]["message"] == rb.json()["error"]["message"]


def test_correct_password_with_wrong_totp_cycles_still_lock(app: FastAPI, api: Api, clk: FakeClock) -> None:
    """S1 review finding 4: a correct password must not reset the failure counter."""
    account = full_admin(api, unique_email("totpcycle"))
    statuses = []
    for _ in range(6):
        with api_client(app) as attacker:
            attacker.post("/auth/login", json={"email": account.email, "password": account.password})
            statuses.append(attacker.post("/auth/mfa/verify", json={"code": "000000"}).status_code)
    assert 423 in statuses
    with api_client(app) as fresh:
        assert _login(fresh, account.email, account.password)[0] == 423


def test_responses_identical_for_existing_and_unknown_emails(app: FastAPI, api: Api) -> None:
    real = register_and_verify(api, unique_email("enum"))
    ghost = unique_email("enumghost")

    def body(path: str, payload: dict[str, str]) -> tuple[int, str]:
        with api_client(app) as fresh:
            r = fresh.post(path, json=payload)
        return r.status_code, r.json().get("message", r.json().get("error", {}).get("message"))

    reg = {"full_name": "E", "workspace_name": "E", "password": STRONG_PASSWORD}
    assert body("/auth/register", {**reg, "email": real.email}) == body(
        "/auth/register", {**reg, "email": ghost}
    )
    assert body("/auth/password/forgot", {"email": real.email}) == body(
        "/auth/password/forgot", {"email": ghost}
    )


def test_csrf_and_origin_are_enforced(api: Api) -> None:
    full_admin(api, unique_email("csrf"))
    body = {"mfa_required": True}
    good = {"Origin": ORIGIN, "X-CSRF-Token": api.csrf(), "If-Match": '"1"'}
    cases = {
        "no token": {"Origin": ORIGIN, "If-Match": '"1"'},
        "wrong token": {**good, "X-CSRF-Token": "0" * 64},
        "evil origin": {**good, "Origin": "https://evil.example"},
        "no origin or referer": {"X-CSRF-Token": good["X-CSRF-Token"], "If-Match": '"1"'},
    }
    for name, headers in cases.items():
        r = api.client.patch("/api/v1/settings", json=body, headers=headers)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_failed"), name
    referer = {**good, "Referer": f"{ORIGIN}/admin/settings"}
    del referer["Origin"]
    assert api.client.patch("/api/v1/settings", json=body, headers=referer).status_code == 200


def test_csrf_token_from_another_session_is_rejected(api: Api, api2: Api) -> None:
    full_admin(api, unique_email("csrfa"))
    full_admin(api2, unique_email("csrfb"))
    foreign = api2.csrf()
    r = api.client.patch(
        "/api/v1/settings", json={"mfa_required": True}, headers={"Origin": ORIGIN, "X-CSRF-Token": foreign}
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_public_posts_need_the_pre_session_csrf_token(app: FastAPI) -> None:
    with api_client(app) as api:
        r = api.client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.test", "password": "x"},
            headers={"Origin": ORIGIN},
        )
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_login_rate_limit_per_ip_and_account(app: FastAPI) -> None:
    email = unique_email("ratelimit")
    with api_client(app) as api:
        codes = [
            api.post("/auth/login", json={"email": email, "password": "x" * 12}).status_code for _ in range(7)
        ]
        last = api.post("/auth/login", json={"email": email, "password": "x" * 12})
    assert 429 in codes and last.status_code == 429 and int(last.headers["Retry-After"]) > 0


def test_register_rate_limit_per_ip(app: FastAPI) -> None:
    with api_client(app) as api:
        codes = [
            api.post(
                "/auth/register",
                json={
                    "email": unique_email("r"),
                    "full_name": "R",
                    "workspace_name": "R",
                    "password": STRONG_PASSWORD,
                },
            ).status_code
            for _ in range(6)
        ]
    assert codes[:5] == [202] * 5 and codes[5] == 429


@pytest.mark.parametrize(
    ("peer", "xff", "trusted", "expected"),
    [
        ("203.0.113.5", None, (), "203.0.113.5"),
        ("203.0.113.5", "1.2.3.4", (), "203.0.113.5"),  # untrusted peer: header ignored
        ("10.0.0.2", "1.2.3.4", ("10.0.0.2",), "1.2.3.4"),
        ("10.0.0.2", "6.6.6.6, 1.2.3.4", ("10.0.0.2",), "1.2.3.4"),  # spoofed left part ignored
        ("10.0.0.2", "1.2.3.4, 10.0.0.3", ("10.0.0.2", "10.0.0.3"), "1.2.3.4"),
        ("10.0.0.2", "garbage", ("10.0.0.2",), None),
    ],
)
def test_client_ip_resolution(
    peer: str, xff: str | None, trusted: tuple[str, ...], expected: str | None
) -> None:
    assert client_ip(peer, xff, trusted) == expected


def test_mfa_code_lockout_revokes_pending_session(app: FastAPI, api: Api) -> None:
    account = full_admin(api, unique_email("mfacount"))
    with api_client(app) as attacker:
        attacker.post("/auth/login", json={"email": account.email, "password": account.password})
        results = [attacker.post("/auth/mfa/verify", json={"code": "123456"}).status_code for _ in range(5)]
        assert account.otpauth_uri
        after = attacker.post("/auth/mfa/verify", json={"code": totp_now(account.otpauth_uri)})
    assert results[-1] == 423
    assert after.status_code in (401, 423)  # pending session gone or account locked: never signed in
