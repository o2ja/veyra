"""DB-level isolation attacks, connected as rebate_app (kickoff report §6.2, D1-D13).

Each D1-D8/D12 test detects a leak with an assertion message starting with ISOLATION-BREACH. With RLS
disabled (negative control) those assertions fail; D9/D10/D13 guard grants and do not depend on RLS.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from rebate.core.ids import new_id
from tests.isolation.conftest import TENANT_TABLES, World, breach


@contextmanager
def as_app(engine: Engine, tenant: UUID | str | None) -> Iterator[Connection]:
    """One rebate_app transaction with ``app.tenant_id`` set (or unset), always rolled back."""
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            if tenant is not None:
                conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
            yield conn
        finally:
            tx.rollback()


def _tenant_values(conn: Connection, table: str) -> set[str]:
    column = TENANT_TABLES[table]
    return {str(v) for v in conn.execute(text(f"SELECT {column} FROM {table}")).scalars()}


@pytest.mark.parametrize("table", sorted(TENANT_TABLES))
def test_d1_select_all_returns_only_own_tenant(app_engine: Engine, world: World, table: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        seen = _tenant_values(conn, table)
    assert str(world.a.tenant_id) in seen, f"{table}: fixture rows for A must be visible to A"
    assert seen == {str(world.a.tenant_id)}, breach(
        f"{table}: A sees rows of {seen - {str(world.a.tenant_id)}}"
    )


def test_d2_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = conn.execute(
            text("SELECT id FROM memberships WHERE id = ANY(:ids)"), {"ids": world.b.membership_ids}
        ).all()
        inv = conn.execute(
            text("SELECT id FROM invitations WHERE id = ANY(:ids)"), {"ids": world.b.invitation_ids}
        ).all()
        tenant = conn.execute(text("SELECT id FROM tenants WHERE id = :b"), {"b": world.b.tenant_id}).all()
    assert rows == [] and inv == [] and tenant == [], breach("A reads B's rows by id")


def test_d3_update_foreign_row_affects_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        n = conn.execute(
            text("UPDATE memberships SET role = 'viewer' WHERE id = ANY(:ids)"),
            {"ids": world.b.membership_ids},
        ).rowcount
        s = conn.execute(
            text("UPDATE tenant_settings SET mfa_required = false WHERE tenant_id = :b"),
            {"b": world.b.tenant_id},
        ).rowcount
    assert n == 0 and s == 0, breach(f"A updated {n} membership(s) and {s} settings row(s) of B")


def test_d4_delete_foreign_row_affects_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        n = conn.execute(
            text("DELETE FROM invitations WHERE id = ANY(:ids)"), {"ids": world.b.invitation_ids}
        ).rowcount
        k = conn.execute(
            text("DELETE FROM idempotency_keys WHERE tenant_id = :b"), {"b": world.b.tenant_id}
        ).rowcount
    assert n == 0 and k == 0, breach(f"A deleted {n} invitation(s) and {k} idempotency key(s) of B")


def _insert_rejected(conn: Connection, sql: str, params: dict[str, object]) -> bool:
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(sql), params)
    except DBAPIError as exc:
        savepoint.rollback()
        assert "row-level security" in str(exc.orig), f"unexpected error: {exc.orig}"
        return True
    savepoint.rollback()
    return False


def test_d5_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World) -> None:
    b = world.b.tenant_id
    with as_app(app_engine, world.a.tenant_id) as conn:
        rejected = {
            "memberships": _insert_rejected(
                conn,
                "INSERT INTO memberships (tenant_id, id, user_id, role) VALUES (:t, :id, :u, 'admin')",
                {"t": b, "id": new_id(), "u": world.a.admin_user_id},
            ),
            "audit_log": _insert_rejected(
                conn,
                "INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type) "
                "VALUES (:t, now(), 'system', 'probe.forged', 'probe')",
                {"t": b},
            ),
            "tenants": _insert_rejected(
                conn,
                "INSERT INTO tenants (id, name, slug) VALUES (:id, 'x', :s)",
                {"id": new_id(), "s": "iso-x"},
            ),
        }
    assert all(rejected.values()), breach(f"inserts into other tenants accepted: {rejected}")


def test_d6_moving_a_row_to_another_tenant_is_rejected(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        savepoint = conn.begin_nested()
        try:
            conn.execute(
                text("UPDATE memberships SET tenant_id = :b WHERE id = :id"),
                {"b": world.b.tenant_id, "id": world.a.membership_ids[0]},
            )
            moved = True
        except DBAPIError as exc:
            assert "row-level security" in str(exc.orig), f"unexpected error: {exc.orig}"
            moved = False
        savepoint.rollback()
    assert not moved, breach("A moved its membership into tenant B")


@pytest.mark.parametrize("setting", [None, ""], ids=["unset", "empty"])
def test_d7_no_tenant_context_sees_nothing(app_engine: Engine, world: World, setting: str | None) -> None:
    with as_app(app_engine, setting) as conn:
        leaked = {table: _tenant_values(conn, table) for table in TENANT_TABLES}
    assert not any(leaked.values()), breach(f"rows visible without tenant context: {leaked}")


def test_d8_unknown_tenant_sees_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, new_id()) as conn:
        leaked = {table: _tenant_values(conn, table) for table in TENANT_TABLES}
    assert not any(leaked.values()), breach(f"rows visible to an unknown tenant: {leaked}")


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY tenant_isolation ON memberships",
        "CREATE POLICY open_up ON memberships USING (true)",
        "SET ROLE rebate_owner",
        "SET ROLE rebate_lookup",
        "UPDATE audit_log SET action = 'x.y'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_d9_app_role_cannot_bypass_rls(app_engine: Engine, world: World, statement: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn, pytest.raises(DBAPIError) as err:
        for part in statement.split("; "):
            conn.execute(text(part))
    message = str(err.value.orig).lower()
    assert any(s in message for s in ("must be owner", "permission denied", "append-only")), message


def test_d9_row_security_off_errors_instead_of_bypassing(app_engine: Engine, world: World) -> None:
    """With row_security=off, a role subject to RLS gets an error instead of unfiltered rows."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        conn.execute(text("SELECT set_config('row_security', 'off', true)"))
        savepoint = conn.begin_nested()
        try:
            rows = conn.execute(text("SELECT tenant_id FROM memberships")).scalars().all()
        except DBAPIError as exc:
            savepoint.rollback()
            assert "row-level security" in str(exc.orig), str(exc.orig)
            return
        savepoint.rollback()
    raise AssertionError(breach(f"row_security=off returned {len(rows)} row(s) unfiltered"))


def test_d10_membership_lookup_returns_only_that_users_rows(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = (
            conn.execute(text("SELECT * FROM fn_user_memberships(:u)"), {"u": world.m_user_id})
            .mappings()
            .all()
        )
        a_admin = (
            conn.execute(text("SELECT tenant_id FROM fn_user_memberships(:u)"), {"u": world.a.admin_user_id})
            .scalars()
            .all()
        )
    assert {str(r["tenant_id"]) for r in rows} == {str(world.a.tenant_id), str(world.b.tenant_id)}
    assert set(rows[0].keys()) == {"tenant_id", "tenant_name", "role", "status"}
    assert [str(t) for t in a_admin] == [str(world.a.tenant_id)]


def test_d10_invitation_lookup_exposes_only_ids(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, None) as conn:
        row = (
            conn.execute(
                text("SELECT * FROM fn_invitation_by_token(:h)"),
                {"h": f"iso-token-{world.b.invitation_ids[0].hex}"},
            )
            .mappings()
            .one()
        )
        none = conn.execute(text("SELECT * FROM fn_invitation_by_token('nope')")).all()
    assert set(row.keys()) == {"tenant_id", "invitation_id"}
    assert row["invitation_id"] == world.b.invitation_ids[0] and none == []


def test_d12_force_rls_applies_to_the_owner(owner_engine: Engine, world: World) -> None:
    with owner_engine.connect() as conn:
        leaked = {table: _tenant_values(conn, table) for table in TENANT_TABLES}
        conn.rollback()
    assert not any(leaked.values()), breach(f"table owner sees rows without tenant context: {leaked}")


@pytest.mark.parametrize("role", ["rebate_readonly", "rebate_ai_writer"])
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM fn_user_memberships(gen_random_uuid())",
        "SELECT * FROM fn_invitation_by_token('x')",
        "SELECT count(*) FROM users",
        "SELECT count(*) FROM sessions",
        "SELECT count(*) FROM user_tokens",
        "SELECT count(*) FROM mfa_recovery_codes",
        "SELECT count(*) FROM pending_registrations",
        "SELECT count(*) FROM rate_limit_counters",
    ],
)
def test_d13_other_roles_cannot_reach_lookups_or_credentials(
    admin_engine: Engine, role: str, statement: str
) -> None:
    with admin_engine.connect() as conn:
        try:
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            with pytest.raises(DBAPIError) as err:
                conn.execute(text(statement))
            assert "permission denied" in str(err.value.orig)
        finally:
            conn.rollback()


def test_d13_idempotency_key_reuse_does_not_cross_tenants(app_engine: Engine, world: World) -> None:
    """The same key value exists in A and B (fixture); A can only ever read its own response."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        bodies = (
            conn.execute(text("SELECT response_body->>'tenant' FROM idempotency_keys WHERE key = 'same-key'"))
            .scalars()
            .all()
        )
    assert bodies == [str(world.a.tenant_id)], breach(f"idempotency replay across tenants: {bodies}")


def test_d13_owner_can_run_the_operator_lookup(owner_engine: Engine, world: World) -> None:
    """Runbook dependency: rebate_owner keeps EXECUTE after ownership moved to rebate_lookup."""
    with owner_engine.connect() as conn:
        sql = text("SELECT tenant_id FROM fn_user_memberships(:u)")
        rows = conn.execute(sql, {"u": world.m_user_id}).all()
        conn.rollback()
    assert len(rows) == 2
