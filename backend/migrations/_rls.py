"""Tenant RLS helper (DATABASE_SCHEMA.md §2). Apply to every tenant-owned table."""

from alembic import op

from migrations._names import checked_table

# NULLIF: once a session has used SET LOCAL, an unset custom GUC reads as '' (not NULL).
# Both cases must fail closed (no rows) rather than error.
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def tenant_rls_sql(table: str, column: str = "tenant_id") -> list[str]:
    t = checked_table(table)
    checked_table(column)
    return [
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {t} "
        f"USING ({column} = {TENANT_EXPR}) WITH CHECK ({column} = {TENANT_EXPR})",
    ]


def enable_tenant_rls(table: str, column: str = "tenant_id") -> None:
    """``column`` is ``id`` only for the ``tenants`` table itself."""
    for statement in tenant_rls_sql(table, column):
        op.execute(statement)
