"""Trust this device for 30 days (2FA bypass cookie)."""

from tests.security.flows import Account, full_admin, step_up
from tests.support import Api, FakeClock, totp_now, unique_email

COOKIE = "__Host-trusted-device"


def _sign_in_trusting(api: Api, account: Account, clk: FakeClock, *, trust: bool) -> None:
    clk.advance(31)  # a TOTP step is single-use
    r = api.post("/auth/login", json={"email": account.email, "password": account.password})
    assert r.json()["status"] == "mfa_required"
    assert account.otpauth_uri
    v = api.post("/auth/mfa/verify", json={"code": totp_now(account.otpauth_uri), "trust_device": trust})
    assert v.status_code == 200, v.text


def _login_status(api: Api, account: Account) -> str:
    r = api.post("/auth/login", json={"email": account.email, "password": account.password})
    assert r.status_code == 200, r.text
    return str(r.json()["status"])


def _trusted_cookie(api: Api) -> str | None:
    return api.client.cookies.get(COOKIE)


def test_trusted_device_cookie_flags_and_login_skips_2fa(api: Api, api2: Api, clk: FakeClock) -> None:
    account = full_admin(api, unique_email("trust"))
    clk.advance(31)
    api2.post("/auth/login", json={"email": account.email, "password": account.password})
    assert account.otpauth_uri
    v = api2.post("/auth/mfa/verify", json={"code": totp_now(account.otpauth_uri), "trust_device": True})
    cookie = next(c for c in v.headers.get_list("set-cookie") if c.startswith(COOKIE + "="))
    for flag in ("httponly", "secure", "samesite=lax", "path=/"):
        assert flag in cookie.lower()
    assert int(cookie.lower().split("max-age=")[1].split(";")[0]) == 30 * 86400
    assert COOKIE + "=" not in v.text

    api2.post("/auth/logout")
    assert _login_status(api2, account) == "signed_in"
    assert api2.get("/settings").status_code == 200
    # no step-up is granted by a trusted-device sign-in
    assert api2.post("/auth/mfa/recovery-codes").status_code == 401


def test_without_the_checkbox_no_cookie_and_2fa_is_still_required(
    api: Api, api2: Api, clk: FakeClock
) -> None:
    account = full_admin(api, unique_email("notrust"))
    _sign_in_trusting(api2, account, clk, trust=False)
    assert _trusted_cookie(api2) is None
    api2.post("/auth/logout")
    assert _login_status(api2, account) == "mfa_required"


def test_expired_or_tampered_trusted_cookie_is_rejected(api: Api, api2: Api, clk: FakeClock) -> None:
    account = full_admin(api, unique_email("expired"))
    _sign_in_trusting(api2, account, clk, trust=True)
    api2.post("/auth/logout")
    for c in api2.client.cookies.jar:
        if c.name == COOKIE:
            c.value = "tampered"
    assert _login_status(api2, account) == "mfa_required"
    api2.post("/auth/logout")

    _sign_in_trusting(api2, account, clk, trust=True)
    api2.post("/auth/logout")
    clk.advance(31 * 86400)
    assert _login_status(api2, account) == "mfa_required"


def test_password_change_and_forget_revoke_trusted_devices(api: Api, api2: Api, clk: FakeClock) -> None:
    account = full_admin(api, unique_email("revoke"))
    _sign_in_trusting(api2, account, clk, trust=True)
    api2.post("/auth/logout")
    step_up(api, account)
    change = api.post(
        "/auth/password/change",
        json={"current_password": account.password, "new_password": "another strong passphrase 99"},
    )
    assert change.status_code == 200
    account.password = "another strong passphrase 99"
    assert _login_status(api2, account) == "mfa_required"

    _sign_in_trusting(api2, account, clk, trust=True)
    api2.post("/auth/logout")
    assert _login_status(api2, account) == "signed_in"
    api2.post("/auth/logout")
    step_up(api, account)
    assert api.post("/auth/trusted-devices/forget").status_code == 204
    assert _login_status(api2, account) == "mfa_required"
