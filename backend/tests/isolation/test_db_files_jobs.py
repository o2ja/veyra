"""S2 database invariants as rebate_app: file identity/outcome immutability, storage-key CHECK, grants on
file_objects and jobs, bounded job attempts (S2 DB review). Not RLS-dependent: these hold with RLS off."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.isolation.conftest import World


@contextmanager
def as_app(engine: Engine, tenant: UUID) -> Iterator[Connection]:
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
            yield conn
        finally:
            tx.rollback()


def _error(conn: Connection, sql: str, params: dict[str, object]) -> str:
    """Run ``sql`` in a savepoint and return the database error text ('' when it succeeded)."""
    sp = conn.begin_nested()
    try:
        conn.execute(text(sql), params)
    except DBAPIError as exc:
        sp.rollback()
        return str(exc.orig)
    sp.rollback()
    return ""


def _make_final(conn: Connection, file_id: UUID, tenant: UUID, status: str) -> None:
    key = f"tenants/{tenant}/imports/2026/11/{file_id}" if status == "clean" else None
    conn.execute(
        text(
            "UPDATE file_objects SET scan_status = :s, scanned_at = now(), "
            "storage_key = coalesce(:k, storage_key) WHERE id = :i"
        ),
        {"s": status, "k": key, "i": file_id},
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("original_name", "'other.csv'"),
        ("sha256", "repeat('b', 64)"),
        ("size_bytes", "99"),
        ("kind", "'attachment'"),
    ],
)
def test_file_identity_columns_are_immutable(
    app_engine: Engine, world: World, column: str, value: str
) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        err = _error(
            conn, f"UPDATE file_objects SET {column} = {value} WHERE id = :i", {"i": world.a.file_ids[0]}
        )
    assert "immutable" in err


@pytest.mark.parametrize("status", ["clean", "infected"])
@pytest.mark.parametrize(
    "change",
    ["scan_status = 'error'", "scan_detail = 'rewritten'", "scanned_at = now() + interval '1 day'"],
)
def test_final_scan_outcomes_never_change(app_engine: Engine, world: World, status: str, change: str) -> None:
    fid, tenant = world.a.file_ids[0], world.a.tenant_id
    with as_app(app_engine, tenant) as conn:
        _make_final(conn, fid, tenant, status)
        err = _error(conn, f"UPDATE file_objects SET {change} WHERE id = :i", {"i": fid})
    assert "is final" in err


def test_storage_key_changes_only_on_promotion_to_clean(app_engine: Engine, world: World) -> None:
    fid, tenant = world.a.file_ids[0], world.a.tenant_id
    other_key = f"tenants/{tenant}/imports/2026/11/{fid}"
    with as_app(app_engine, tenant) as conn:
        err = _error(
            conn, "UPDATE file_objects SET storage_key = :k WHERE id = :i", {"k": other_key, "i": fid}
        )
        assert "only on promotion" in err
        promoted = _error(
            conn,
            "UPDATE file_objects SET storage_key = :k, scan_status = 'clean', scanned_at = now() WHERE id = :i",
            {"k": other_key, "i": fid},
        )
        assert promoted == ""


def test_storage_key_must_belong_to_the_row(app_engine: Engine, world: World) -> None:
    fid = world.a.file_ids[0]
    foreign = f"tenants/{world.b.tenant_id}/imports/2026/11/{fid}"
    with as_app(app_engine, world.a.tenant_id) as conn:
        err = _error(
            conn,
            "UPDATE file_objects SET storage_key = :k, scan_status = 'clean', scanned_at = now() WHERE id = :i",
            {"k": foreign, "i": fid},
        )
    assert "ck_file_objects_key_prefix" in err


def test_scanned_at_agrees_with_status(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        err = _error(
            conn, "UPDATE file_objects SET scan_status = 'error' WHERE id = :i", {"i": world.a.file_ids[0]}
        )
    assert "ck_file_objects_scanned" in err


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM file_objects WHERE id = :i",
        "TRUNCATE file_objects",
        "DELETE FROM jobs",
        "TRUNCATE jobs",
    ],
)
def test_app_role_cannot_delete_files_or_jobs(app_engine: Engine, world: World, statement: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        err = _error(conn, statement, {"i": world.a.file_ids[0]})
    assert "permission denied" in err


def test_a_final_file_may_be_archived_once_and_nothing_else(app_engine: Engine, world: World) -> None:
    fid, tenant = world.a.file_ids[0], world.a.tenant_id
    with as_app(app_engine, tenant) as conn:
        _make_final(conn, fid, tenant, "clean")
        assert _error(conn, "UPDATE file_objects SET archived_at = now() WHERE id = :i", {"i": fid}) == ""
        conn.execute(text("UPDATE file_objects SET archived_at = now() WHERE id = :i"), {"i": fid})
        assert "is final" in _error(
            conn, "UPDATE file_objects SET archived_at = NULL WHERE id = :i", {"i": fid}
        )
        err = _error(
            conn,
            "UPDATE file_objects SET scan_detail = 'x', archived_at = archived_at WHERE id = :i",
            {"i": fid},
        )
        assert "is final" in err


def test_guarded_file_delete_reaches_only_the_sessions_workspace(app_engine: Engine, world: World) -> None:
    """``fn_delete_unreferenced_file`` deletes by the session's app.tenant_id (explicitly, not only through RLS)."""
    foreign = world.b.file_ids[0]
    with as_app(app_engine, world.a.tenant_id) as conn:
        assert (
            conn.execute(text("SELECT fn_delete_unreferenced_file(:i)"), {"i": foreign}).scalar_one() is False
        )
    with as_app(app_engine, world.b.tenant_id) as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM file_objects WHERE id = :i"), {"i": foreign}).scalar_one()
            == 1
        )
    with app_engine.connect() as conn, conn.begin():
        err = _error(conn, "SELECT fn_delete_unreferenced_file(:i)", {"i": foreign})  # no workspace set
    assert "no workspace context" in err


def test_job_attempts_are_bounded_by_the_schema(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        err = _error(
            conn,
            "INSERT INTO jobs (id, kind, payload, attempts, max_attempts) "
            "VALUES (gen_random_uuid(), 'x_probe', '{}', 6, 5)",
            {},
        )
    assert "ck_jobs_attempts_max" in err
