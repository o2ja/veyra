"""Pooled-connection context leakage (kickoff report §6.2, P1-P5).

The engine has exactly one physical connection, so every transaction reuses it. ``transaction()`` from
core/db.py is the only way application code opens a session; these tests drive it directly.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from rebate.core.db import transaction
from tests.conftest import DbUrls
from tests.isolation.conftest import World, breach

TxFactory = Callable[[Engine, UUID | None], AbstractContextManager[Session]]


@pytest.fixture
def single_conn_engine(migrated_db: DbUrls) -> Iterator[Engine]:
    engine = create_engine(migrated_db.app, pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


def _visible_tenants(session: Session) -> set[str]:
    return {str(t) for t in session.execute(text("SELECT tenant_id FROM memberships")).scalars()}


def _setting(session: Session) -> str | None:
    value = session.execute(text("SELECT NULLIF(current_setting('app.tenant_id', true), '')")).scalar_one()
    return None if value is None else str(value)


def setting_survives_check(engine: Engine, world: World, tx: TxFactory) -> None:
    """Independent of RLS: the tenant setting itself must not outlive its transaction."""
    with tx(engine, world.a.tenant_id) as s:
        assert _setting(s) == str(world.a.tenant_id)
    with tx(engine, None) as s:
        setting = _setting(s)
    assert setting is None, breach(f"tenant setting survived the transaction: {setting}")


def test_p1_context_does_not_survive_commit(single_conn_engine: Engine, world: World) -> None:
    setting_survives_check(single_conn_engine, world, transaction)
    with transaction(single_conn_engine, world.a.tenant_id) as s:
        seen_a = _visible_tenants(s)
    with transaction(single_conn_engine, None) as s:
        seen_none = _visible_tenants(s)
    assert seen_a == {str(world.a.tenant_id)}, breach(f"A's transaction sees {seen_a}")
    assert seen_none == set(), breach(f"next transaction without tenant sees {seen_none}")


def test_p2_context_does_not_survive_rollback(single_conn_engine: Engine, world: World) -> None:
    with pytest.raises(RuntimeError), transaction(single_conn_engine, world.a.tenant_id):
        raise RuntimeError("handler failed midway")
    with transaction(single_conn_engine, world.b.tenant_id) as s:
        setting, seen = _setting(s), _visible_tenants(s)
    assert setting == str(world.b.tenant_id)
    assert seen == {str(world.b.tenant_id)}, breach(f"B's transaction after A's rollback sees {seen}")


def test_p3_interleaved_transactions_never_leak(single_conn_engine: Engine, world: World) -> None:
    cycle: list[UUID | None] = [world.a.tenant_id, None, world.b.tenant_id, None]
    for i in range(200):
        tenant = cycle[i % len(cycle)]
        with transaction(single_conn_engine, tenant) as s:
            seen = _visible_tenants(s)
        expected = set() if tenant is None else {str(tenant)}
        assert seen == expected, breach(f"iteration {i} (tenant {tenant}) sees {seen}")


@contextmanager
def _buggy_session_level_set(engine: Engine, tenant_id: UUID | None) -> Iterator[Session]:
    """The bug P1 must catch: session-scoped SET instead of SET LOCAL semantics."""
    with Session(engine) as session, session.begin():
        if tenant_id is not None:
            session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})
        yield session


def test_p5_p1_detects_session_level_set(single_conn_engine: Engine, world: World) -> None:
    """Mutation check: the check used by P1 catches the session-scoped SET bug."""
    with pytest.raises(AssertionError, match="tenant setting survived"):
        setting_survives_check(single_conn_engine, world, _buggy_session_level_set)
    # leave the pooled connection clean for later tests
    with single_conn_engine.connect() as conn:
        conn.execute(text("RESET app.tenant_id"))
        conn.commit()
