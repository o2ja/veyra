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
TEST_DB = "rebate_test"
ROLES = ("rebate_owner", "rebate_app", "rebate_ai_writer", "rebate_readonly")


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
    return db_urls


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
