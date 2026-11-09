"""End-to-end auth flows through the real API, real Postgres and real Mailpit (no shortcuts)."""

from dataclasses import dataclass, field

from tests.support import STRONG_PASSWORD, Api, latest_token, messages_to, totp_now


@dataclass
class Account:
    email: str
    password: str = STRONG_PASSWORD
    otpauth_uri: str | None = None
    recovery_codes: list[str] = field(default_factory=list)


def register_and_verify(api: Api, email: str, workspace: str = "Acme Electric (SYNTHETIC)") -> Account:
    before = len(messages_to(email, timeout=0.2, expect=0))
    r = api.post(
        "/auth/register",
        json={
            "email": email,
            "full_name": "Test User",
            "workspace_name": workspace,
            "password": STRONG_PASSWORD,
        },
    )
    assert r.status_code == 202, r.text
    token = latest_token(email, expect=before + 1)
    v = api.post("/auth/verify-email", json={"token": token})
    assert v.status_code == 200, v.text
    return Account(email=email)


def login(api: Api, account: Account) -> str:
    r = api.post("/auth/login", json={"email": account.email, "password": account.password})
    assert r.status_code == 200, r.text
    status: str = r.json()["status"]
    if status == "mfa_required":
        assert account.otpauth_uri
        m = api.post("/auth/mfa/verify", json={"code": totp_now(account.otpauth_uri)})
        assert m.status_code == 200, m.text
    return status


def enable_mfa(api: Api, account: Account) -> None:
    setup = api.post("/auth/mfa/setup")
    assert setup.status_code == 200, setup.text
    account.otpauth_uri = setup.json()["otpauth_uri"]
    enable = api.post("/auth/mfa/enable", json={"code": totp_now(account.otpauth_uri)})
    assert enable.status_code == 200, enable.text
    account.recovery_codes = enable.json()["recovery_codes"]


def full_admin(api: Api, email: str, workspace: str = "Acme Electric (SYNTHETIC)") -> Account:
    """Registered, verified, signed in, MFA enabled (the workspace requires it by default)."""
    account = register_and_verify(api, email, workspace)
    login(api, account)
    enable_mfa(api, account)
    return account


def step_up(api: Api, account: Account) -> None:
    assert account.otpauth_uri
    r = api.post(
        "/auth/reauthenticate",
        json={"password": account.password, "mfa_code": totp_now(account.otpauth_uri)},
    )
    assert r.status_code == 200, r.text
