"""The lint passes on the real schema and catches each kind of violation.

The bad tables live in a scratch schema inside a transaction that is always rolled back
(CLAUDE.md §11 exception for tests).
"""

from sqlalchemy import Engine, text

from migrations._rls import tenant_rls_sql
from tests.db.schema_lint import lint_schema

SCRATCH = "lint_scratch"


def test_real_schema_is_clean(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        assert lint_schema(conn, "public") == []


def test_lint_detects_each_violation(owner_engine: Engine) -> None:
    ddl = [
        f"CREATE SCHEMA {SCRATCH}",
        f"CREATE TABLE {SCRATCH}.floats (id uuid PRIMARY KEY, a real, b double precision, "
        "c money, d double precision[], e numeric(18,4))",
        f"CREATE TABLE {SCRATCH}.good (tenant_id uuid NOT NULL, id uuid PRIMARY KEY, "
        "CONSTRAINT uq_good UNIQUE (tenant_id, id))",
        *tenant_rls_sql(f"{SCRATCH}.good"),
        f"CREATE TABLE {SCRATCH}.no_rls (tenant_id uuid, id uuid, UNIQUE (tenant_id, id))",
        f"CREATE TABLE {SCRATCH}.not_forced (tenant_id uuid, id uuid, UNIQUE (tenant_id, id))",
        f"ALTER TABLE {SCRATCH}.not_forced ENABLE ROW LEVEL SECURITY",
        f"CREATE TABLE {SCRATCH}.no_unique (tenant_id uuid, id uuid PRIMARY KEY)",
        *tenant_rls_sql(f"{SCRATCH}.no_unique"),
        f"CREATE TABLE {SCRATCH}.child (tenant_id uuid, id uuid, parent_id uuid, other_id uuid, "
        "UNIQUE (tenant_id, id), "
        f"CONSTRAINT fk_ok FOREIGN KEY (tenant_id, parent_id) REFERENCES {SCRATCH}.good (tenant_id, id), "
        f"CONSTRAINT fk_bad FOREIGN KEY (other_id) REFERENCES {SCRATCH}.good (id))",
        *tenant_rls_sql(f"{SCRATCH}.child"),
        # No `id` column: exempt from UNIQUE (tenant_id, id), still needs RLS.
        f"CREATE TABLE {SCRATCH}.keyed (tenant_id uuid PRIMARY KEY)",
        *tenant_rls_sql(f"{SCRATCH}.keyed"),
        f"CREATE TABLE {SCRATCH}.keyed_no_rls (tenant_id uuid PRIMARY KEY)",
        # `tenants` has no tenant_id column but must still have RLS.
        f"CREATE TABLE {SCRATCH}.tenants (id uuid PRIMARY KEY)",
    ]
    with owner_engine.connect() as conn:
        try:
            for statement in ddl:
                conn.execute(text(statement))
            found = lint_schema(conn, SCRATCH)
        finally:
            conn.rollback()

    assert found == sorted(
        [
            "child: foreign key fk_bad to tenant table good is not composite on tenant_id",
            "floats.a: forbidden type real",
            "floats.b: forbidden type double precision",
            "floats.c: forbidden type money",
            "floats.d: forbidden type double precision[]",
            "keyed_no_rls: row level security not enabled and forced",
            "tenants: row level security not enabled and forced",
            "no_rls: row level security not enabled and forced",
            "no_unique: missing UNIQUE (tenant_id, id)",
            "not_forced: row level security not enabled and forced",
        ]
    )
