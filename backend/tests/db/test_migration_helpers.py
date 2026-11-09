"""RLS and append-only helpers behave as specified when exercised as the runtime role.

Runs as superuser only to SET ROLE inside one rolled-back transaction (CLAUDE.md §11 test exception).
"""

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from migrations._immutable import append_only_sql
from migrations._rls import tenant_rls_sql

S = "helper_scratch"
A = "00000000-0000-7000-8000-00000000000a"
B = "00000000-0000-7000-8000-00000000000b"


def _set_tenant(conn: Connection, tenant: str | None) -> None:
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant or ""})


def _setup(conn: Connection) -> None:
    conn.execute(text(f"CREATE SCHEMA {S} AUTHORIZATION rebate_owner"))
    conn.execute(text("SET LOCAL ROLE rebate_owner"))  # objects owned like a real migration
    conn.execute(text(f"CREATE TABLE {S}.items (tenant_id uuid NOT NULL, id int, UNIQUE (tenant_id, id))"))
    for statement in tenant_rls_sql(f"{S}.items"):
        conn.execute(text(statement))
    conn.execute(text(f"CREATE TABLE {S}.events (tenant_id uuid NOT NULL, id int)"))
    conn.execute(text(f"GRANT USAGE ON SCHEMA {S} TO rebate_app"))
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {S}.items, {S}.events TO rebate_app"))
    for statement in append_only_sql(f"{S}.events"):
        conn.execute(text(statement))
    conn.execute(text("SET LOCAL ROLE rebate_app"))


def test_rls_scopes_rows_to_current_tenant_and_fails_closed(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        try:
            _setup(conn)
            for tenant, ids in ((A, (1, 2)), (B, (3,))):
                _set_tenant(conn, tenant)
                for i in ids:
                    conn.execute(text(f"INSERT INTO {S}.items VALUES (:t, :i)"), {"t": tenant, "i": i})

            _set_tenant(conn, A)
            assert conn.execute(text(f"SELECT id FROM {S}.items ORDER BY id")).scalars().all() == [
                1,
                2,
            ]
            assert conn.execute(text(f"UPDATE {S}.items SET id = id WHERE id = 3")).rowcount == 0
            assert conn.execute(text(f"DELETE FROM {S}.items WHERE id = 3")).rowcount == 0

            _set_tenant(conn, None)  # unset ('' after first use) -> no rows, no error
            assert conn.execute(text(f"SELECT count(*) FROM {S}.items")).scalar() == 0

            _set_tenant(conn, A)
            conn.execute(text("SAVEPOINT cross_write"))
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(text(f"INSERT INTO {S}.items VALUES (:t, 9)"), {"t": B})
            conn.execute(text("ROLLBACK TO SAVEPOINT cross_write"))
        finally:
            conn.rollback()


@pytest.mark.parametrize(
    "statement",
    [f"UPDATE {S}.events SET id = 2", f"DELETE FROM {S}.events", f"TRUNCATE {S}.events"],
)
def test_append_only_rejects_mutation_even_for_owner(admin_engine: Engine, statement: str) -> None:
    with admin_engine.connect() as conn:
        try:
            _setup(conn)
            conn.execute(text(f"INSERT INTO {S}.events VALUES (:t, 1)"), {"t": A})
            conn.execute(text("SAVEPOINT as_app"))
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(statement))  # app role lost the privilege
            conn.execute(text("ROLLBACK TO SAVEPOINT as_app"))
            conn.execute(text("SET LOCAL ROLE rebate_owner"))
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text(statement))  # trigger stops even the owner
        finally:
            conn.rollback()


def test_helpers_reject_unsafe_identifiers() -> None:
    with pytest.raises(ValueError):
        tenant_rls_sql("items; DROP TABLE x")
    with pytest.raises(ValueError):
        append_only_sql("Items")
