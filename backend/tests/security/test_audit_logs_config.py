"""Audit (same transaction, allow-lists, denials), secrets never in logs, temporary-mailer guards,
config validation, DB-session lint (FR-AU-01/02; SECURITY.md §5, §7, §8; kickoff §5, review 22/23/26)."""

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.modules import audit
from rebate.modules.audit import Action, Actor
from rebate.modules.tenancy import check_four_eyes, permissions_for
from rebate.modules.tenancy import service as tenancy_service
from tests.conftest import DbUrls
from tests.security.flows import Account, enable_mfa, full_admin, login
from tests.support import (
    MAILPIT,
    STRONG_PASSWORD,
    Api,
    api_client,
    drain,
    latest_token,
    make_app,
    make_settings,
    unique_email,
)

SRC = Path(__file__).resolve().parents[2] / "src"
_K32 = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="  # base64 of 32 bytes


def _tenant_of(api: Api) -> UUID:
    return UUID(api.get("/auth/me").json()["tenant"]["id"])


def test_audit_entry_commits_or_rolls_back_with_the_change(api: Api, app_engine: Engine) -> None:
    admin = full_admin(api, unique_email("sametx"))
    tenant = _tenant_of(api)
    user_id = UUID(api.get("/auth/me").json()["user"]["id"])
    meta = {"request_id": "test", "ip": None, "user_agent": None}

    class Abort(Exception):
        pass

    with pytest.raises(Abort), transaction(app_engine, tenant) as db:
        row = tenancy_service.get_settings(db, tenant)
        tenancy_service.update_settings(
            db,
            tenant_id=tenant,
            actor_id=user_id,
            expected_revision=row.revision,
            changes={"retention_years": 11},
            meta=meta,
        )
        raise Abort
    with transaction(app_engine, tenant) as db:
        assert tenancy_service.get_settings(db, tenant).retention_years == 7
        assert (
            db.execute(
                text("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND action = 'settings.updated'"),
                {"t": tenant},
            ).scalar_one()
            == 0
        )
    assert admin.email


def test_audit_allow_list_drops_secret_fields(api: Api, app_engine: Engine) -> None:
    full_admin(api, unique_email("allowlist"))
    tenant = _tenant_of(api)
    with transaction(app_engine, tenant) as db:
        audit.record(
            db,
            tenant_id=tenant,
            actor=Actor("system"),
            action=Action.AUTH_EMAIL_CHANGED,
            entity_type="user",
            before={
                "email": "old@example.test",
                "password_hash": "x",
                "mfa_secret_enc": "y",
                "token_hash": "z",
            },
            after={"email": "new@example.test", "code_hash": "w"},
        )
        stored = db.execute(
            text("SELECT before, after FROM audit_log WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"),
            {"t": tenant},
        ).one()
        with pytest.raises(ValueError, match="allow-list"):
            audit.record(
                db,
                tenant_id=tenant,
                actor=Actor("system"),
                action=Action.AUTH_LOGOUT,
                entity_type="unlisted",
                before={"a": 1},
            )
    assert stored.before == {"email": "old@example.test"} and stored.after == {"email": "new@example.test"}


def test_no_secret_column_ever_reaches_the_audit_log(admin_engine: Engine) -> None:
    """Runs after the flows above in the same session: scans every stored before/after."""
    with admin_engine.connect() as conn:
        keys = (
            conn.execute(
                text(
                    "SELECT DISTINCT k FROM audit_log, LATERAL ("
                    " SELECT jsonb_object_keys(before) WHERE jsonb_typeof(before) = 'object'"
                    " UNION SELECT jsonb_object_keys(after) WHERE jsonb_typeof(after) = 'object') AS x(k)"
                )
            )
            .scalars()
            .all()
        )
    # "code" alone is the master-data business identifier (manufacturer/customer/branch code, e.g. VOLT);
    # anything else naming a code (recovery_code, code_hash, ...) is still refused.
    business = {"code"}
    assert not {k for k in keys if re.search(r"hash|secret|token|password|code", k)} - business


def test_permission_denials_are_audited_and_rate_limited(app: FastAPI, api: Api) -> None:
    full_admin(api, unique_email("denyadmin"))
    viewer_email = unique_email("denyviewer")
    api.post("/invitations", json={"email": viewer_email, "role": "viewer"})
    with api_client(app) as viewer:
        viewer.post(
            "/invitations/accept",
            json={"token": latest_token(viewer_email), "full_name": "V", "password": STRONG_PASSWORD},
        )
        account = Account(email=viewer_email)
        login(viewer, account)
        enable_mfa(viewer, account)
        for _ in range(3):
            assert (
                viewer.post("/invitations", json={"email": unique_email("z"), "role": "viewer"}).status_code
                == 403
            )
        viewer_id = viewer.get("/auth/me").json()["user"]["id"]
    denials = api.get("/audit-log", params={"action": "authz.denied", "actor_user_id": viewer_id}).json()[
        "data"
    ]
    assert len(denials) == 1
    assert denials[0]["after"] == {
        "permission": "invitation.manage",
        "method": "POST",
        "route": "/api/v1/invitations",
    }


def test_audit_search_filters_and_pagination(api: Api) -> None:
    admin = full_admin(api, unique_email("search"))
    first = api.get("/audit-log", params={"limit": 2}).json()
    assert len(first["data"]) == 2 and first["page"]["has_more"] is True
    second = api.get("/audit-log", params={"limit": 2, "cursor": first["page"]["next_cursor"]}).json()
    assert {e["id"] for e in first["data"]}.isdisjoint({e["id"] for e in second["data"]})
    assert api.get("/audit-log", params={"bogus": "1"}).status_code == 400
    assert api.get("/audit-log", params={"cursor": "%%%"}).status_code == 422
    created = api.get("/audit-log", params={"action": "tenant.created"}).json()["data"]
    assert len(created) == 1 and created[0]["entity_type"] == "tenant"
    assert (created[0]["actor_email"], created[0]["actor_name"]) == (admin.email, "Test User")


def test_secrets_never_reach_the_logs(
    migrated_db: DbUrls, clk: object, capsys: pytest.CaptureFixture[str]
) -> None:
    ghost = unique_email("ghostlog")
    app = make_app(migrated_db.app)  # created after capsys starts, so its JSON logs are captured
    with api_client(app) as api:
        account = full_admin(api, unique_email("logscan"))
        setup_secret = re.search(r"secret=([A-Z2-7]+)", account.otpauth_uri or "")
        invitee = unique_email("loginvitee")
        api.post("/invitations", json={"email": invitee, "role": "viewer"})
        invite_token = latest_token(invitee)
        api.post("/auth/password/forgot", json={"email": account.email})
        api.post("/auth/login", json={"email": ghost, "password": "wrong wrong wrong"})
        api.post(
            "/invitations/accept", json={"token": invite_token, "full_name": "L", "password": STRONG_PASSWORD}
        )
    drain()  # email and reset jobs log from the worker side
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert any(e.get("event") == "request" for e in lines)
    app_logs = json.dumps(lines)
    forbidden = [invite_token, STRONG_PASSWORD, ghost, "otpauth://", "data:image/svg+xml"]
    if setup_secret:
        forbidden.append(setup_secret.group(1))
    for value in forbidden:
        assert value not in app_logs, f"leaked into logs: {value[:12]}…"
    unknown = [e for e in lines if e.get("event") == "auth.login_failed_unknown"]
    assert unknown and all(len(e["email_hash"]) == 16 for e in unknown)


def test_smtp_failure_is_logged_without_address(
    migrated_db: DbUrls, capsys: pytest.CaptureFixture[str]
) -> None:
    app = make_app(migrated_db.app, smtp_host="127.0.0.1", smtp_port=9)  # nothing listens there
    email = unique_email("smtpfail")
    with api_client(app) as api:
        r = api.post(
            "/auth/register",
            json={"email": email, "full_name": "S", "workspace_name": "S", "password": STRONG_PASSWORD},
        )
        drain()
    assert r.status_code == 202  # the request does not depend on SMTP (and never claims delivery)
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    failures = [e for e in lines if e.get("event") == "email.send_failed"]
    assert failures and failures[0]["template"] == "verify_email"
    assert email not in json.dumps(lines)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_hosted_environments_require_smtp_starttls(env: str) -> None:
    with pytest.raises(ValidationError, match="SMTP_STARTTLS"):
        make_settings(
            "postgresql+psycopg://x@y/z",
            app_env=env,
            trusted_proxy_ips="10.0.0.1",
            public_base_url="https://app.example.com",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"encryption_keys": "k1:YWFhYWFhYWFhYWFhYWFhYQ=="}, "32 bytes"),
        ({"encryption_keys": "k1:short"}, "base64"),
        ({"encryption_keys": "nocolon"}, "kid:base64key"),
        (
            {"encryption_keys": f"k1:{_K32},k1:{_K32}"},
            "duplicate",
        ),
        ({"public_base_url": "https://app.example.com/path"}, "origin"),
        ({"trusted_proxy_ips": "not-an-ip"}, "IP"),
    ],
)
def test_config_rejects_bad_security_values(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings("postgresql+psycopg://x@y/z", **overrides)


def test_mailpit_is_the_only_mail_target_in_tests() -> None:
    assert make_settings("postgresql+psycopg://x@y/z").smtp_port == 1025
    assert MAILPIT.endswith(":8025")


def test_four_eyes_and_permission_registry() -> None:
    maker, other = UUID(int=1), UUID(int=2)
    check_four_eyes(required=True, maker_user_ids={maker}, approver_user_id=other)
    check_four_eyes(required=False, maker_user_ids={maker}, approver_user_id=maker)
    with pytest.raises(Exception, match="different person"):
        check_four_eyes(required=True, maker_user_ids={maker}, approver_user_id=maker)
    assert "user.manage" in permissions_for("admin") and "user.manage" not in permissions_for("approver")
    assert "audit.read" not in permissions_for("analyst") and "audit.read" in permissions_for("viewer")
    assert not any(p.endswith((".manage", ".write", ".run", ".approve")) for p in permissions_for("viewer"))
    with pytest.raises(ValueError):
        permissions_for("owner")


ALLOWED_SESSION_SITES = {
    "rebate/core/db.py",  # transaction(), assert_safe_role()
    "rebate/api/deps.py",  # the request transaction and the denial-audit transaction
    "rebate/api/health.py",  # readiness probe (SELECT 1, no tenant data)
}


def test_db_sessions_are_opened_only_in_sanctioned_places() -> None:
    """S1 review finding 23: tenant context is set only where sessions are opened."""
    pattern = re.compile(r"\bSession\(|\.connect\(|\.begin\(\)")
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if pattern.search(path.read_text(encoding="utf-8")) and rel not in ALLOWED_SESSION_SITES:
            offenders.append(rel)
    assert not offenders, f"open DB sessions only via core.db.transaction / the request context: {offenders}"


def test_no_mutation_markers_left_in_source() -> None:
    """Breakage spot-checks mark temporary edits with MUTATION; none may reach a commit (review finding 1)."""
    hits = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "MUTATION" in p.read_text(encoding="utf-8")
    ]
    assert not hits, hits
