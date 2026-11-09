"""Isolation fixtures: two tenants (A, B) with rows in every tenant table, plus user M in both.

Seeded once per session through the superuser connection (tests only; bypasses RLS on purpose so the
seed itself cannot be filtered). Every attack then runs as ``rebate_app``, the role the API uses.

Assertions that detect a cross-tenant leak start with ``BREACH`` (see ``breach()``): the negative
control (infra/scripts/isolation_negative_control.py) requires exactly those assertion failures when
RLS is switched off, so a test that fails for any other reason does not count as "detected".
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from rebate.core.ids import new_id

BREACH = "ISOLATION-BREACH"

# Every tenant table and the column holding the tenant (tenants is keyed by its own id).
TENANT_TABLES: dict[str, str] = {
    "tenants": "id",
    "tenant_settings": "tenant_id",
    "memberships": "tenant_id",
    "invitations": "tenant_id",
    "audit_log": "tenant_id",
    "idempotency_keys": "tenant_id",
}


def breach(detail: str) -> str:
    return f"{BREACH}: {detail}"


@dataclass
class TenantData:
    tenant_id: UUID
    admin_user_id: UUID
    membership_ids: list[UUID] = field(default_factory=list)
    invitation_ids: list[UUID] = field(default_factory=list)


@dataclass
class World:
    a: TenantData
    b: TenantData
    m_user_id: UUID  # member of both A (analyst) and B (admin)


def _user(conn: Connection, email: str) -> UUID:
    uid = new_id()
    conn.execute(
        text(
            "INSERT INTO users (id, email, password_hash, full_name, email_verified_at) "
            "VALUES (:id, :email, 'x', :name, now())"
        ),
        {"id": uid, "email": email, "name": email.split("@")[0]},
    )
    return uid


def _tenant(conn: Connection, name: str, admin_email: str) -> TenantData:
    tid = new_id()
    admin = _user(conn, admin_email)
    conn.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
        {"id": tid, "n": name, "s": f"iso-{tid.hex[-12:]}"},
    )
    conn.execute(text("INSERT INTO tenant_settings (tenant_id) VALUES (:t)"), {"t": tid})
    data = TenantData(tenant_id=tid, admin_user_id=admin)
    data.membership_ids.append(_membership(conn, tid, admin, "admin"))
    inv = new_id()
    conn.execute(
        text(
            "INSERT INTO invitations (tenant_id, id, email, role, token_hash, invited_by, expires_at) "
            "VALUES (:t, :id, :e, 'viewer', :h, :by, :exp)"
        ),
        {
            "t": tid,
            "id": inv,
            "e": f"invitee-{tid.hex[:8]}@example.test",
            "h": f"iso-token-{inv.hex}",
            "by": admin,
            "exp": datetime.now(UTC) + timedelta(days=7),
        },
    )
    data.invitation_ids.append(inv)
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, occurred_at, actor_type, actor_user_id, action, "
            "entity_type, entity_id) VALUES (:t, now(), 'user', :u, 'tenant.created', 'tenant', :t)"
        ),
        {"t": tid, "u": admin},
    )
    conn.execute(
        text(
            "INSERT INTO idempotency_keys (tenant_id, key, endpoint, request_hash, response_status, "
            "response_body, expires_at) VALUES (:t, 'same-key', '/probe', 'h', 200, "
            "jsonb_build_object('tenant', CAST(:t AS text)), now() + interval '1 day')"
        ),
        {"t": tid},
    )
    return data


def _membership(conn: Connection, tid: UUID, uid: UUID, role: str) -> UUID:
    mid = new_id()
    conn.execute(
        text("INSERT INTO memberships (tenant_id, id, user_id, role) VALUES (:t, :id, :u, :r)"),
        {"t": tid, "id": mid, "u": uid, "r": role},
    )
    return mid


@pytest.fixture(scope="session")
def world(admin_engine: Engine) -> World:
    suffix = new_id().hex[:8]
    with admin_engine.begin() as conn:
        a = _tenant(conn, "Tenant A (SYNTHETIC)", f"admin-a-{suffix}@example.test")
        b = _tenant(conn, "Tenant B (SYNTHETIC)", f"admin-b-{suffix}@example.test")
        m = _user(conn, f"member-m-{suffix}@example.test")
        a.membership_ids.append(_membership(conn, a.tenant_id, m, "analyst"))
        b.membership_ids.append(_membership(conn, b.tenant_id, m, "admin"))
    return World(a=a, b=b, m_user_id=m)


def tenant_ids_in(rows: Iterable[object]) -> set[str]:
    return {str(r) for r in rows}
