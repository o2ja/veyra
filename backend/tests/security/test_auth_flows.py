"""Registration, verification, login, MFA, logout, reset (W1; SECURITY.md §1; kickoff §8)."""

from sqlalchemy import Engine, text

from tests.security.flows import enable_mfa, full_admin, login, register_and_verify, step_up
from tests.support import (
    ORIGIN,
    STRONG_PASSWORD,
    TOKEN_RE,
    Api,
    FakeClock,
    latest_token,
    messages_to,
    totp_now,
    unique_email,
)


def test_register_verify_login_and_forced_mfa_enrolment(api: Api) -> None:
    email = unique_email("w1")
    account = register_and_verify(api, email)
    assert login(api, account) == "signed_in"
    me = api.get("/auth/me").json()
    assert me["enrollment_required"] is True and me["permissions"] == []  # workspace requires MFA
    blocked = api.get("/settings")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "mfa_enrollment_required"

    enable_mfa(api, account)
    assert len(account.recovery_codes) == 10 and len(set(account.recovery_codes)) == 10
    me = api.get("/auth/me").json()
    assert me["enrollment_required"] is False and me["role"] == "admin"
    assert "user.manage" in me["permissions"] and me["tenant"]["name"] == "Acme Electric (SYNTHETIC)"
    assert api.get("/settings").status_code == 200


def test_session_cookie_flags_and_no_token_in_body(api: Api) -> None:
    account = register_and_verify(api, unique_email("cookie"))
    r = api.post("/auth/login", json={"email": account.email, "password": account.password})
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("__Host-session="))
    csrf = next(c for c in cookies if c.startswith("__Host-csrf="))
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag.lower() in session.lower()
    assert "httponly" not in csrf.lower() and "secure" in csrf.lower()
    token = session.split(";")[0].split("=", 1)[1]
    assert token not in r.text
    assert r.headers["Cache-Control"] == "no-store"


def test_db_stores_only_the_session_hash(api: Api, admin_engine: Engine) -> None:
    account = register_and_verify(api, unique_email("hash"))
    login(api, account)
    raw = api.client.cookies.get("__Host-session")
    assert raw
    with admin_engine.connect() as conn:
        ids = (
            conn.execute(
                text("SELECT s.id FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = :e"),
                {"e": account.email},
            )
            .scalars()
            .all()
        )
    assert ids and raw not in ids and all(len(i) == 64 for i in ids)


def test_mfa_login_totp_replay_and_recovery_code_single_use(api: Api, api2: Api, clk: FakeClock) -> None:
    account = full_admin(api, unique_email("mfa"))
    r = api2.post("/auth/login", json={"email": account.email, "password": account.password})
    assert r.json()["status"] == "mfa_required"
    pending = api2.get("/settings")
    assert pending.status_code == 401 and pending.json()["error"]["code"] == "mfa_required"

    assert account.otpauth_uri
    code = totp_now(account.otpauth_uri)
    assert api2.post("/auth/mfa/verify", json={"code": code}).status_code == 200
    assert api2.get("/settings").status_code == 200

    # same code again (replay) in a fresh login is rejected
    api2.post("/auth/logout")
    api2.post("/auth/login", json={"email": account.email, "password": account.password})
    replay = api2.post("/auth/mfa/verify", json={"code": code})
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "mfa_required"

    recovery = account.recovery_codes[0]
    assert api2.post("/auth/mfa/verify", json={"code": recovery}).status_code == 200
    api2.post("/auth/logout")
    api2.post("/auth/login", json={"email": account.email, "password": account.password})
    again = api2.post("/auth/mfa/verify", json={"code": recovery})
    assert again.status_code == 401


def test_pre_mfa_session_expires_after_ten_minutes(api: Api, api2: Api, clk: FakeClock) -> None:
    account = full_admin(api, unique_email("pending"))
    api2.post("/auth/login", json={"email": account.email, "password": account.password})
    clk.advance(11 * 60)
    assert account.otpauth_uri
    r = api2.post("/auth/mfa/verify", json={"code": totp_now(account.otpauth_uri)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_logout_revokes_the_session_server_side(api: Api) -> None:
    full_admin(api, unique_email("logout"))
    stolen = api.client.cookies.get("__Host-session")
    assert stolen and api.post("/auth/logout").status_code == 204
    api.client.cookies.clear()
    r = api.client.get("/api/v1/auth/me", headers={"Cookie": f"__Host-session={stolen}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_session_survives_a_browser_restart_then_expires(api: Api, clk: FakeClock) -> None:
    full_admin(api, unique_email("idle"))
    clk.advance(13 * 86400)  # browser closed for 13 days: the persistent cookie still works
    assert api.get("/settings").status_code == 200
    clk.advance(15 * 86400)
    idle = api.get("/settings")
    assert idle.status_code == 401 and idle.json()["error"]["code"] == "session_expired"


def test_absolute_timeout_despite_activity(api: Api, clk: FakeClock) -> None:
    full_admin(api, unique_email("absolute"))
    for _ in range(13):
        clk.advance(86400)
        assert api.get("/settings").status_code == 200
    clk.advance(2 * 86400)  # now past 14 days
    r = api.get("/settings")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_session_cookie_is_persistent_for_14_days(api: Api) -> None:
    account = register_and_verify(api, unique_email("maxage"))
    r = api.post("/auth/login", json={"email": account.email, "password": account.password})
    session = next(c for c in r.headers.get_list("set-cookie") if c.startswith("__Host-session="))
    max_age = int(session.lower().split("max-age=")[1].split(";")[0])
    assert 14 * 86400 - 60 <= max_age <= 14 * 86400


def test_session_rotates_on_mfa_and_the_pending_session_dies(api: Api) -> None:
    account = full_admin(api, unique_email("rotate"))
    api.post("/auth/logout")
    api.post("/auth/login", json={"email": account.email, "password": account.password})
    pending = api.client.cookies.get("__Host-session")
    assert account.otpauth_uri
    assert api.post("/auth/mfa/verify", json={"code": totp_now(account.otpauth_uri)}).status_code == 200
    full = api.client.cookies.get("__Host-session")
    assert pending and full and pending != full
    api.client.cookies.clear()
    replay = api.client.get("/api/v1/auth/me", headers={"Cookie": f"__Host-session={pending}"})
    assert replay.status_code == 401


def test_pre_login_cookie_is_never_accepted_after_login(api: Api) -> None:
    """Session fixation: an attacker-planted cookie value never becomes a valid session."""
    account = register_and_verify(api, unique_email("fixation"))
    planted = "attacker-chosen-value-0123456789abcdef"
    csrf = api.csrf()
    r = api.client.post(
        "/api/v1/auth/login",
        json={"email": account.email, "password": account.password},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
            "Cookie": f"__Host-session={planted}; __Host-csrf={csrf}",
        },
    )
    assert r.status_code == 200
    issued = next(c for c in r.headers.get_list("set-cookie") if c.startswith("__Host-session="))
    assert planted not in issued
    api.client.cookies.clear()
    probe = api.client.get("/api/v1/auth/me", headers={"Cookie": f"__Host-session={planted}"})
    assert probe.status_code == 401


def test_password_reset_single_use_revokes_sessions_and_does_not_sign_in(api: Api, api2: Api) -> None:
    account = full_admin(api, unique_email("reset"))
    assert api2.post("/auth/password/forgot", json={"email": account.email}).status_code == 202
    token = latest_token(account.email, expect=2)  # verify mail, then reset mail
    new_password = "a completely different passphrase 77"
    r = api2.post("/auth/password/reset", json={"token": token, "new_password": new_password})
    assert r.status_code == 200
    assert api2.get("/auth/me").status_code == 401  # reset does not sign in
    assert api.get("/auth/me").status_code == 401  # the old browser session was revoked
    again = api2.post("/auth/password/reset", json={"token": token, "new_password": new_password + "x"})
    assert again.status_code == 410 and again.json()["error"]["code"] == "link_expired"
    account.password = new_password
    assert api2.post("/auth/login", json={"email": account.email, "password": new_password}).json()[
        "status"
    ] == (
        "mfa_required"  # MFA kept after reset
    )


def test_reset_token_expires_after_thirty_minutes(api: Api, api2: Api, clk: FakeClock) -> None:
    account = register_and_verify(api, unique_email("resetexp"))
    api2.post("/auth/password/forgot", json={"email": account.email})
    token = latest_token(account.email, expect=2)
    clk.advance(31 * 60)
    r = api2.post("/auth/password/reset", json={"token": token, "new_password": "brand new passphrase 2026"})
    assert r.status_code == 410


def test_weak_and_common_passwords_rejected(api: Api) -> None:
    base = {"email": unique_email("weak"), "full_name": "W", "workspace_name": "W"}
    short = api.post("/auth/register", json={**base, "password": "short pw"})
    assert short.status_code == 422 and short.json()["error"]["details"][0]["code"] == "length"
    common_pw = "123qweasdzxc"  # gitleaks:allow (deliberately common test password)
    common = api.post("/auth/register", json={**base, "password": common_pw})
    assert common.status_code == 422 and common.json()["error"]["code"] == "password_too_common"
    same = api.post("/auth/register", json={**base, "password": base["email"]})
    assert same.status_code == 422 and same.json()["error"]["code"] == "password_too_common"


def test_step_up_required_for_recovery_codes_and_password_change(api: Api) -> None:
    account = full_admin(api, unique_email("stepup"))
    step_up(api, account)
    codes = api.post("/auth/mfa/recovery-codes")
    assert codes.status_code == 200 and len(codes.json()["recovery_codes"]) == 10
    change = api.post(
        "/auth/password/change",
        json={"current_password": STRONG_PASSWORD, "new_password": "another strong passphrase 99"},
    )
    assert change.status_code == 200


def test_step_up_window_expires(api: Api, clk: FakeClock) -> None:
    full_admin(api, unique_email("stepexp"))
    clk.advance(6 * 60)
    r = api.post("/auth/mfa/recovery-codes")
    assert r.status_code == 401 and r.json()["error"]["code"] == "reauthentication_required"


def test_verify_link_single_use_and_unknown(api: Api) -> None:
    email = unique_email("verifyonce")
    api.post(
        "/auth/register",
        json={"email": email, "full_name": "V", "workspace_name": "V", "password": STRONG_PASSWORD},
    )
    token = latest_token(email)
    body = {"token": token, "password": STRONG_PASSWORD}
    wrong = api.post("/auth/verify-email", json={**body, "password": "not the registration password"})
    assert wrong.status_code == 410  # the link alone is not enough (review finding 8) ...
    assert api.post("/auth/verify-email", json=body).status_code == 200  # ... and stays usable
    assert api.post("/auth/verify-email", json=body).status_code == 410
    assert api.post("/auth/verify-email", json={**body, "token": "x" * 43}).status_code == 410


def test_unverified_account_does_not_exist_and_cannot_be_hijacked(api: Api, api2: Api) -> None:
    """S1 review finding 1: the attacker registers the victim's address first."""
    victim = unique_email("victim")
    attacker_pw = "attacker chosen passphrase 1"
    api2.post(
        "/auth/register",
        json={"email": victim, "full_name": "Mallory", "workspace_name": "Evil", "password": attacker_pw},
    )
    # attacker cannot sign in (no account exists until the link is used) and gets the generic answer
    r = api2.post("/auth/login", json={"email": victim, "password": attacker_pw})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    # the real owner registers and verifies with their own link: the account has the owner's password
    account = register_and_verify(api, victim, workspace="Victim Co (SYNTHETIC)")
    assert login(api, account) == "signed_in"
    assert api2.post("/auth/login", json={"email": victim, "password": attacker_pw}).status_code == 401
    # the attacker's pending link (the oldest email) no longer works
    oldest = messages_to(victim, expect=2)[-1]
    attacker_token = TOKEN_RE.search(oldest["Text"])
    assert attacker_token
    stale = {"token": attacker_token.group(1), "password": attacker_pw}
    assert api2.post("/auth/verify-email", json=stale).status_code == 410


def test_registering_an_existing_email_sends_a_notice_not_a_link(api: Api) -> None:
    account = register_and_verify(api, unique_email("exists"))
    r = api.post(
        "/auth/register",
        json={"email": account.email, "full_name": "X", "workspace_name": "X", "password": STRONG_PASSWORD},
    )
    assert r.status_code == 202
    newest = messages_to(account.email, expect=2)[0]
    assert newest["Subject"] == "Someone tried to register with your email"
    assert "#token=" not in newest["Text"]


def test_victim_cannot_complete_an_attackers_registration(api: Api, api2: Api) -> None:
    """Review finding 8: the owner of the address clicks the verify mail the attacker triggered."""
    victim = unique_email("victim2")
    api2.post(
        "/auth/register",
        json={
            "email": victim,
            "full_name": "M",
            "workspace_name": "Evil",
            "password": "attacker chosen pw 77",
        },
    )
    token = latest_token(victim)
    # the victim does not know the attacker's password: whatever they type, no account is created
    r = api.post("/auth/verify-email", json={"token": token, "password": STRONG_PASSWORD})
    assert r.status_code == 410
    assert (
        api2.post("/auth/login", json={"email": victim, "password": "attacker chosen pw 77"}).status_code
        == 401
    )


def test_mfa_setup_and_enable_need_a_recent_step_up(api: Api, clk: FakeClock) -> None:
    """Review finding 2: a stolen session must not be able to bind the attacker's authenticator."""
    account = register_and_verify(api, unique_email("mfastepup"))
    login(api, account)
    clk.advance(6 * 60)
    r = api.post("/auth/mfa/setup")
    assert r.status_code == 401 and r.json()["error"]["code"] == "reauthentication_required"
    assert api.post("/auth/reauthenticate", json={"password": account.password}).status_code == 200
    enable_mfa(api, account)
    # enabling MFA does not count as a fresh step-up
    clk.advance(6 * 60)
    assert api.post("/auth/mfa/recovery-codes").status_code == 401


def test_password_reset_voids_a_pending_email_change(app: object, api: Api, api2: Api) -> None:
    """Review finding 3: the notice tells the owner to reset; the reset must stop the change."""
    account = full_admin(api, unique_email("hijackmail"))
    assert account.otpauth_uri
    new_email = unique_email("attackeraddr")
    r = api.post(
        "/auth/email/change",
        json={
            "new_email": new_email,
            "password": account.password,
            "mfa_code": totp_now(account.otpauth_uri),
        },
    )
    assert r.status_code == 202
    change_token = latest_token(new_email)
    api2.post("/auth/password/forgot", json={"email": account.email})
    reset = latest_token(account.email, expect=3)
    assert (
        api2.post(
            "/auth/password/reset", json={"token": reset, "new_password": "owner took it back 2026"}
        ).status_code
        == 200
    )
    assert api2.post("/auth/email/confirm", json={"token": change_token}).status_code == 410


def test_login_rehashes_when_argon2_parameters_change(migrated_db: object, admin_engine: Engine) -> None:
    """Kickoff §4: rehash on login so parameters can be retuned without forcing resets."""
    from tests.support import api_client, make_app

    email = unique_email("rehash")
    with api_client(make_app(migrated_db.app)) as a:  # type: ignore[attr-defined]
        register_and_verify(a, email)
    with admin_engine.connect() as conn:
        before = conn.execute(
            text("SELECT password_hash FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
    with api_client(make_app(migrated_db.app, argon2_time_cost=2)) as b:  # type: ignore[attr-defined]
        assert b.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD}).status_code == 200
    with admin_engine.connect() as conn:
        after = conn.execute(
            text("SELECT password_hash FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
    assert "t=1" in before and "t=2" in after
