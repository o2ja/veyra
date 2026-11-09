"""Append-only helper (DATABASE_SCHEMA.md §7): trigger rejects UPDATE/DELETE/TRUNCATE, app role
loses the privileges. Uses ``fn_forbid_mutation()`` from the baseline migration."""

from alembic import op

from migrations._names import bare, checked_table


def append_only_sql(table: str, app_role: str = "rebate_app") -> list[str]:
    t, name = checked_table(table), bare(table)
    checked_table(app_role)
    return [
        f"CREATE TRIGGER trg_{name}_no_mutation BEFORE UPDATE OR DELETE ON {t} "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()",
        f"CREATE TRIGGER trg_{name}_no_truncate BEFORE TRUNCATE ON {t} "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON {t} FROM {app_role}",
    ]


def make_append_only(table: str) -> None:
    for statement in append_only_sql(table):
        op.execute(statement)
