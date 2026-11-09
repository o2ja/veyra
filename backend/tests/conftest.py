"""Test harness. DB tests run against a real PostgreSQL: a fresh ``rebate_test`` database is created
from empty and migrated to head once per session (that is the empty->head migration test).

Needs: TEST_ADMIN_DATABASE_URL (superuser, tests only), REBATE_OWNER_PASSWORD, REBATE_APP_PASSWORD,
and the roles from infra/scripts/init-db.sh.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get("REBATE_TEST_DB", "rebate_test")
# Negative control (CLAUDE.md §11 exception, S1 D-1): only in the throwaway database `rebate_negctl`,
# RLS is disabled after migrating, by a dedicated role that can connect to that database only.
NEGCTL_DB = "rebate_negctl"
NEGCTL = os.environ.get("REBATE_NEGCTL") == "1"
if NEGCTL and TEST_DB != NEGCTL_DB:
    raise RuntimeError("REBATE_NEGCTL=1 is only allowed with REBATE_TEST_DB=rebate_negctl")
ROLES = ("rebate_owner", "rebate_app", "rebate_ai_writer", "rebate_readonly", "rebate_lookup")


@dataclass(frozen=True)
class DbUrls:
    admin: str
    owner: str
    app: str


def alembic_config(owner_url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def db_urls() -> DbUrls:
    base = make_url(os.environ["TEST_ADMIN_DATABASE_URL"])

    def as_role(user: str, password_env: str) -> str:
        url = base.set(username=user, password=os.environ[password_env], database=TEST_DB)
        return url.render_as_string(hide_password=False)

    admin_url = base.set(database=TEST_DB).render_as_string(hide_password=False)
    return DbUrls(
        admin=admin_url,
        owner=as_role("rebate_owner", "REBATE_OWNER_PASSWORD"),
        app=as_role("rebate_app", "REBATE_APP_PASSWORD"),
    )


@pytest.fixture(scope="session")
def migrated_db(db_urls: DbUrls) -> DbUrls:
    server = create_engine(
        make_url(os.environ["TEST_ADMIN_DATABASE_URL"]).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with server.connect() as conn:
        present = set(
            conn.execute(text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:r)"), {"r": list(ROLES)})
            .scalars()
            .all()
        )
        missing = set(ROLES) - present
        if missing:
            pytest.fail(f"roles missing {sorted(missing)}: run `make db-init` first")
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB} OWNER rebate_owner"))
        conn.execute(text(f"REVOKE ALL ON DATABASE {TEST_DB} FROM PUBLIC"))
        conn.execute(
            text(f"GRANT CONNECT ON DATABASE {TEST_DB} TO rebate_app, rebate_ai_writer, rebate_readonly")
        )
    server.dispose()
    command.upgrade(alembic_config(db_urls.owner), "head")
    if NEGCTL:
        _disable_rls_in_throwaway_db(db_urls)
    return db_urls


def _disable_rls_in_throwaway_db(db_urls: DbUrls) -> None:
    """Switch RLS off on every tenant table of `rebate_negctl` via the dedicated role
    `rebate_negctl_ddl` (CONNECT on rebate_negctl only), then drop that role again."""
    import secrets

    assert TEST_DB == NEGCTL_DB
    password = secrets.token_urlsafe(24)
    server = create_engine(
        make_url(os.environ["TEST_ADMIN_DATABASE_URL"]).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with server.connect() as conn:
        conn.execute(text("DROP ROLE IF EXISTS rebate_negctl_ddl"))
        conn.execute(text(f"CREATE ROLE rebate_negctl_ddl LOGIN PASSWORD '{password}'"))
        conn.execute(text(f"GRANT CONNECT ON DATABASE {NEGCTL_DB} TO rebate_negctl_ddl"))
        conn.execute(text("GRANT rebate_owner TO rebate_negctl_ddl WITH INHERIT FALSE, SET TRUE"))
    ddl_url = make_url(db_urls.owner).set(username="rebate_negctl_ddl", password=password)
    ddl = create_engine(ddl_url)
    try:
        with ddl.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == NEGCTL_DB
            conn.execute(text("SET LOCAL ROLE rebate_owner"))
            tables = (
                conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
                    )
                )
                .scalars()
                .all()
            )
            for table in tables:
                conn.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    finally:
        ddl.dispose()
        with server.connect() as conn:
            conn.execute(text(f"REVOKE CONNECT ON DATABASE {NEGCTL_DB} FROM rebate_negctl_ddl"))
            conn.execute(text("DROP ROLE rebate_negctl_ddl"))
        server.dispose()


def _engine(url: str) -> Iterator[Engine]:
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def admin_engine(migrated_db: DbUrls) -> Iterator[Engine]:
    yield from _engine(migrated_db.admin)


@pytest.fixture(scope="session")
def owner_engine(migrated_db: DbUrls) -> Iterator[Engine]:
    yield from _engine(migrated_db.owner)


@pytest.fixture(scope="session")
def app_engine(migrated_db: DbUrls) -> Iterator[Engine]:
    yield from _engine(migrated_db.app)
