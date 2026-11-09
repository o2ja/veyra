"""Database engine, per-transaction tenant context, and the startup role self-check."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

OWNER_ROLE = "rebate_owner"

# Constraint names per DATABASE_SCHEMA.md §1 (pk_/fk_/uq_/ck_/ix_).
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all module models; Alembic compares against its metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UnsafeDatabaseRole(RuntimeError):
    """The runtime connection could bypass RLS or owns the schema. The process must not start."""


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def transaction(engine: Engine, tenant_id: UUID | None = None) -> Iterator[Session]:
    """One transaction. ``app.tenant_id`` is set with SET LOCAL semantics, so it never outlives it."""
    with Session(engine) as session, session.begin():
        if tenant_id is not None:
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        yield session


_ROLE_CHECK = text(
    """
    SELECT r.rolname,
           r.rolsuper,
           r.rolbypassrls,
           d.datdba = r.oid AS owns_database,
           COALESCE((SELECT pg_has_role(current_user, o.oid, 'MEMBER')
                     FROM pg_roles o WHERE o.rolname = :owner), false) AS is_owner_member,
           EXISTS (SELECT 1 FROM pg_class c
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relowner = r.oid
                     AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                     AND n.nspname NOT LIKE 'pg_toast%') AS owns_relations
    FROM pg_roles r, pg_database d
    WHERE r.rolname = current_user AND d.datname = current_database()
    """
)


def assert_safe_role(engine: Engine) -> None:
    """Refuse superuser, BYPASSRLS, database owner, rebate_owner members and object owners."""
    with engine.connect() as conn:
        row = conn.execute(_ROLE_CHECK, {"owner": OWNER_ROLE}).mappings().one()
    problems = [
        name
        for name in (
            "rolsuper",
            "rolbypassrls",
            "owns_database",
            "is_owner_member",
            "owns_relations",
        )
        if row[name]
    ]
    if problems:
        raise UnsafeDatabaseRole(
            f"refusing to run as database role {row['rolname']!r}: {', '.join(problems)}"
        )
