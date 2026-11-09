"""core.db: tenant context is transaction-local (no bleed on a pooled connection); role self-check."""

import uuid

import pytest
from sqlalchemy import Engine, create_engine, text

from rebate.core.db import UnsafeDatabaseRole, assert_safe_role, transaction
from tests.conftest import DbUrls

CURRENT = text("SELECT NULLIF(current_setting('app.tenant_id', true), '')")


def test_tenant_context_does_not_leak_across_transactions(migrated_db: DbUrls) -> None:
    engine = create_engine(migrated_db.app, pool_size=1, max_overflow=0)  # same connection reused
    tenant = uuid.uuid4()
    try:
        with transaction(engine, tenant) as session:
            assert session.execute(CURRENT).scalar() == str(tenant)
            first_pid = session.execute(text("SELECT pg_backend_pid()")).scalar()
        with transaction(engine) as session:
            assert session.execute(text("SELECT pg_backend_pid()")).scalar() == first_pid
            assert session.execute(CURRENT).scalar() is None
    finally:
        engine.dispose()


def test_failed_transaction_rolls_back_and_clears_context(migrated_db: DbUrls) -> None:
    engine = create_engine(migrated_db.app, pool_size=1, max_overflow=0)
    try:
        with pytest.raises(RuntimeError), transaction(engine, uuid.uuid4()):
            raise RuntimeError("boom")
        with transaction(engine) as session:
            assert session.execute(CURRENT).scalar() is None
    finally:
        engine.dispose()


def test_self_check_accepts_app_role(app_engine: Engine) -> None:
    assert_safe_role(app_engine)


@pytest.mark.parametrize(("which", "reason"), [("owner", "owns_database"), ("admin", "rolsuper")])
def test_self_check_refuses_privileged_roles(migrated_db: DbUrls, which: str, reason: str) -> None:
    engine = create_engine(getattr(migrated_db, which))
    try:
        with pytest.raises(UnsafeDatabaseRole, match=reason):
            assert_safe_role(engine)
    finally:
        engine.dispose()
