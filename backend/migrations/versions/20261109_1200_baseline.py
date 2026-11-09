"""baseline: extensions, schema privileges, default grants, immutability trigger function

Revision ID: 20261109_1200_baseline
Revises:
"""

from alembic import op

revision = "20261109_1200_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trusted extensions: creatable by the database owner (also created by init-db.sh).
    for ext in ("btree_gist", "pg_trgm", "citext"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")

    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO rebate_app, rebate_ai_writer, rebate_readonly")

    # Tables created later by rebate_owner: app gets DML (append-only tables revoke UPDATE/DELETE
    # via _immutable.py); readonly gets SELECT (still tenant-scoped by RLS). The AI writer gets
    # explicit per-table grants only (DATABASE_SCHEMA.md §2).
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rebate_owner IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rebate_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rebate_owner IN SCHEMA public "
        "GRANT SELECT ON TABLES TO rebate_readonly"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rebate_owner IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO rebate_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rebate_owner IN SCHEMA public "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )

    op.execute(
        """
        CREATE FUNCTION fn_forbid_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'table %.% is append-only: % rejected',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END
        $$
        """
    )
