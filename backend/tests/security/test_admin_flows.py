"""Invitations, members, roles, settings, workspace switching, email change (W2; FR-ID-02/04/05/07/08)."""

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests.security.flows import Account, enable_mfa, full_admin, login, step_up
from tests.support import (
    STRONG_PASSWORD,
    Api,
    FakeClock,
    api_client,
    latest_token,
    messages_to,
    totp_now,
    unique_email,
)


def invite(api: Api, email: str, role: str) -> str:
    r = api.post("/invitations", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return latest_token(email)


def accept_new(api: Api, token: str, name: str = "Invitee") -> None:
    r = api.post("/invitations/accept", json={"token": token, "full_name": name, "password": STRONG_PASSWORD})
    assert r.status_code == 200, r.text


_stack = ExitStack()


def member(app: FastAPI, email: str, admin: Api, role: str) -> tuple[Api, Account]:
    """Invite + accept + sign in + MFA in a new browser (closed by the module teardown)."""
    token = invite(admin, email, role)
    api = _stack.enter_context(api_client(app))
    accept_new(api, token)
    account = Account(email=email)
    login(api, account)
    enable_mfa(api, account)
    return api, account


@pytest.fixture(autouse=True, scope="module")
def _close_member_clients() -> Iterator[None]:
    yield
    _stack.close()


def test_admin_invites_analyst_and_viewer_with_role_limits(app: FastAPI, api: Api) -> None:
    full_admin(api, unique_email("owner"))
    analyst, _ = member(app, unique_email("analyst"), api, "analyst")
    viewer, _ = member(app, unique_email("viewer"), api, "viewer")
    assert analyst.get("/auth/me").json()["role"] == "analyst"
    assert viewer.get("/settings").status_code == 200  # settings.read for all
    patch = viewer.patch("/settings", json={"mfa_required": True}, headers={"If-Match": '"1"'})
    assert patch.status_code == 403 and patch.json()["error"]["code"] == "forbidden"
    assert viewer.get("/audit-log").status_code == 200
    assert analyst.get("/audit-log").status_code == 403  # SECURITY.md §7: A P V only
    assert (
        analyst.post("/invitations", json={"email": unique_email("x"), "role": "viewer"}).status_code == 403
    )
    users = api.get("/users").json()["data"]
    assert sorted(u["role"] for u in users) == ["admin", "analyst", "viewer"]


def test_invitation_single_use_expiry_and_revocation(app: FastAPI, api: Api, clk: FakeClock) -> None:
    full_admin(api, unique_email("invadmin"))
    email = unique_email("once")
    token = invite(api, email, "viewer")
    with api_client(app) as b:
        accept_new(b, token)
        again = b.post(
            "/invitations/accept", json={"token": token, "full_name": "X", "password": STRONG_PASSWORD}
        )
    assert again.status_code == 410 and again.json()["error"]["code"] == "invitation_expired"

    expiring = invite(api, unique_email("late"), "viewer")
    clk.advance(timedelta(days=7, minutes=1).total_seconds())
    with api_client(app) as c:
        late = c.post(
            "/invitations/accept", json={"token": expiring, "full_name": "L", "password": STRONG_PASSWORD}
        )
    assert late.status_code == 410


def test_revoked_invitation_cannot_be_accepted(app: FastAPI, api: Api) -> None:
    full_admin(api, unique_email("revadmin"))
    email = unique_email("revoked")
    token = invite(api, email, "viewer")
    inv_id = api.get("/invitations").json()["data"][0]["id"]
    assert api.delete(f"/invitations/{inv_id}").status_code == 204
    with api_client(app) as b:
        r = b.post(
            "/invitations/accept", json={"token": token, "full_name": "R", "password": STRONG_PASSWORD}
        )
    assert r.status_code == 410


def test_existing_user_must_sign_in_to_accept(app: FastAPI, api: Api, api2: Api) -> None:
    full_admin(api, unique_email("adminA"))
    other = full_admin(api2, unique_email("adminB"), workspace="Other Co (SYNTHETIC)")
    token = invite(api, other.email, "approver")
    with api_client(app) as anonymous:
        r = anonymous.post("/invitations/accept", json={"token": token})
    assert r.status_code == 401
    assert api2.post("/invitations/accept", json={"token": token}).status_code == 200
    names = sorted(w["name"] for w in api2.get("/auth/tenants").json()["data"])
    assert names == ["Acme Electric (SYNTHETIC)", "Other Co (SYNTHETIC)"]


def test_invite_response_is_the_same_for_members_and_strangers(api: Api) -> None:
    owner = full_admin(api, unique_email("same"))
    a = api.post("/invitations", json={"email": owner.email, "role": "viewer"})
    b = api.post("/invitations", json={"email": unique_email("stranger"), "role": "viewer"})
    assert a.status_code == b.status_code == 201
    assert set(a.json()) == set(b.json())


def test_admin_promotion_requires_step_up(app: FastAPI, api: Api, clk: FakeClock) -> None:
    admin = full_admin(api, unique_email("promote"))
    analyst, _ = member(app, unique_email("promotee"), api, "analyst")
    uid = analyst.get("/auth/me").json()["user"]["id"]
    clk.advance(6 * 60)
    r = api.patch(f"/users/{uid}", json={"role": "admin"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "reauthentication_required"
    step_up(api, admin)
    assert api.patch(f"/users/{uid}", json={"role": "admin"}).json()["role"] == "admin"


def test_role_change_applies_on_the_next_request(app: FastAPI, api: Api) -> None:
    full_admin(api, unique_email("rolechange"))
    analyst, _ = member(app, unique_email("demoted"), api, "analyst")
    uid = analyst.get("/auth/me").json()["user"]["id"]
    assert "import.run" in analyst.get("/auth/me").json()["permissions"]
    api.patch(f"/users/{uid}", json={"role": "viewer"})
    assert "import.run" not in analyst.get("/auth/me").json()["permissions"]


def test_deactivation_ends_sessions_in_that_workspace_only(app: FastAPI, api: Api, api2: Api) -> None:
    full_admin(api, unique_email("deactA"))
    m_email = unique_email("multi")
    m, m_account = member(app, m_email, api, "analyst")
    other = full_admin(api2, unique_email("deactB"), workspace="B Co (SYNTHETIC)")
    token_b = invite(api2, m_email, "viewer")
    with api_client(app) as m_in_b:
        login(m_in_b, m_account)
        assert m_in_b.post("/invitations/accept", json={"token": token_b}).status_code == 200
        b_id = next(
            w["tenant_id"]
            for w in m_in_b.get("/auth/tenants").json()["data"]
            if w["name"] == "B Co (SYNTHETIC)"
        )
        assert m_in_b.post("/auth/switch-tenant", json={"tenant_id": b_id}).status_code == 200
        uid = m.get("/auth/me").json()["user"]["id"]
        assert api.patch(f"/users/{uid}", json={"status": "disabled"}).json()["status"] == "disabled"
        r = m.get("/auth/me")
        assert r.status_code == 401  # session in A revoked at once (FR-ID-05)
        assert m_in_b.get("/auth/me").json()["tenant"]["name"] == "B Co (SYNTHETIC)"  # B untouched
    assert other.email


def test_last_admin_cannot_be_demoted_or_disabled(api: Api) -> None:
    full_admin(api, unique_email("lastadmin"))
    uid = api.get("/auth/me").json()["user"]["id"]
    for change in ({"role": "viewer"}, {"status": "disabled"}):
        r = api.patch(f"/users/{uid}", json=change)
        assert r.status_code == 409, change
    assert api.get("/auth/me").json()["role"] == "admin"


def test_settings_if_match_and_audit_before_after(api: Api) -> None:
    full_admin(api, unique_email("settings"))
    current = api.get("/settings")
    etag = current.headers["ETag"]
    missing = api.patch("/settings", json={"business_timezone": "America/New_York"})
    assert missing.status_code == 412
    ok = api.patch("/settings", json={"business_timezone": "America/New_York"}, headers={"If-Match": etag})
    assert ok.status_code == 200 and ok.json()["revision"] == current.json()["revision"] + 1
    stale = api.patch("/settings", json={"four_eyes_required": False}, headers={"If-Match": etag})
    assert stale.status_code == 412
    bad = api.patch(
        "/settings", json={"business_timezone": "Mars/Base"}, headers={"If-Match": ok.headers["ETag"]}
    )
    assert bad.status_code == 422
    smuggled = api.patch(
        "/settings", json={"tenant_id": "x", "currency_code": "EUR"}, headers={"If-Match": '"2"'}
    )
    assert smuggled.status_code == 422
    entry = api.get("/audit-log", params={"action": "settings.updated"}).json()["data"][0]
    assert entry["before"] == {"business_timezone": "America/Chicago"}
    assert entry["after"] == {"business_timezone": "America/New_York"}


def test_switch_tenant_rotates_session_and_rejects_non_members(app: FastAPI, api: Api, api2: Api) -> None:
    full_admin(api, unique_email("switchA"))
    m_email = unique_email("switcher")
    m, m_account = member(app, m_email, api, "viewer")
    full_admin(api2, unique_email("switchB"), workspace="Switch B (SYNTHETIC)")
    token = invite(api2, m_email, "analyst")
    assert m.post("/invitations/accept", json={"token": token}).status_code == 200
    b_id = next(
        w["tenant_id"] for w in m.get("/auth/tenants").json()["data"] if w["name"] == "Switch B (SYNTHETIC)"
    )
    old = m.client.cookies.get("__Host-session")
    assert m.post("/auth/switch-tenant", json={"tenant_id": b_id}).status_code == 200
    assert m.client.cookies.get("__Host-session") != old
    assert m.get("/auth/me").json()["role"] == "analyst"
    a_tenant = api.get("/auth/me").json()["tenant"]["id"]
    stranger_b = api2.post("/auth/switch-tenant", json={"tenant_id": a_tenant})
    assert stranger_b.status_code == 404
    assert m_account.email


def test_email_change_requires_password_and_mfa_and_revokes_sessions(
    app: FastAPI, api: Api, api2: Api
) -> None:
    account = full_admin(api, unique_email("emailchg"))
    login(api2, account)
    new_email = unique_email("newaddr")
    assert account.otpauth_uri
    no_mfa = api.post("/auth/email/change", json={"new_email": new_email, "password": account.password})
    assert no_mfa.status_code == 422
    wrong_pw = api.post(
        "/auth/email/change",
        json={
            "new_email": new_email,
            "password": "not my password at all",
            "mfa_code": totp_now(account.otpauth_uri),
        },
    )
    assert wrong_pw.status_code == 401
    ok = api.post(
        "/auth/email/change",
        json={
            "new_email": new_email,
            "password": account.password,
            "mfa_code": totp_now(account.otpauth_uri),
        },
    )
    assert ok.status_code == 202
    notice = messages_to(account.email, expect=2)[0]
    assert notice["Subject"] == "Your Veyra Recover email is being changed"
    token = latest_token(new_email)
    with api_client(app) as anon:
        assert anon.post("/auth/email/confirm", json={"token": token}).status_code == 200
    assert api.get("/auth/me").status_code == 401 and api2.get("/auth/me").status_code == 401
    account.email = new_email
    with api_client(app) as fresh:
        assert login(fresh, account) == "mfa_required"


def test_email_change_to_a_taken_address_reveals_nothing(api: Api, api2: Api) -> None:
    account = full_admin(api, unique_email("taken1"))
    other = full_admin(api2, unique_email("taken2"))
    assert account.otpauth_uri
    r = api.post(
        "/auth/email/change",
        json={
            "new_email": other.email,
            "password": account.password,
            "mfa_code": totp_now(account.otpauth_uri),
        },
    )
    assert r.status_code == 202
    newest = messages_to(other.email, expect=2)[0]
    assert newest["Subject"] == "Email change request for Veyra Recover" and "#token=" not in newest["Text"]


def test_invitation_rows_store_only_the_token_hash(api: Api, admin_engine: Engine) -> None:
    full_admin(api, unique_email("hashinv"))
    email = unique_email("hashed")
    token = invite(api, email, "viewer")
    with admin_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT token_hash FROM invitations WHERE email = :e"), {"e": email}
        ).scalar_one()
    assert stored != token and len(stored) == 64
