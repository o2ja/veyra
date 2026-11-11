"""``rebate_ai_writer`` and ``rebate_app`` privileges on the extraction tables, proven with the real roles on a
real database (catalog check of every table/column, then actual statements). DATABASE_SCHEMA.md §2: the AI
role may read the extraction tables (and nothing of file_objects: the job reads files as the app role) and write only extraction results and its usage ledger;
it must not be able to touch anything else (in particular drafts, versions, master data, users)."""

from collections.abc import Iterator
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import CursorResult, Engine, text
from sqlalchemy.exc import DBAPIError

from rebate.core.db import make_engine, transaction
from tests.conftest import DbUrls
from tests.extraction.conftest import ai_url
from tests.extraction.support import golden_file, set_ai, start
from tests.extraction.test_golden import SPA_884
from tests.integration.conftest import Workspace, clean_file
from tests.support import drain

RESULT_COLUMNS = {
    "status", "text_method", "pass_results", "result", "evidence_check", "validation", "confidence",
    "model", "prompt_version", "error_code", "error_detail", "completed_at",
}  # fmt: skip
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
AI_TABLE_GRANTS = {  # the complete list of table-level privileges of the AI role
    ("document_extractions", "SELECT"),
    ("ai_invocations", "SELECT"),
    ("ai_invocations", "INSERT"),
}


@pytest.fixture
def ai(migrated_db: DbUrls) -> Iterator[Engine]:
    engine = make_engine(ai_url(migrated_db))
    yield engine
    engine.dispose()


@pytest.fixture
def admin(migrated_db: DbUrls) -> Iterator[Engine]:
    engine = make_engine(migrated_db.admin)
    yield engine
    engine.dispose()


def _tables(admin: Engine) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
            ).scalars()
        )


def test_the_ai_role_has_exactly_the_documented_table_privileges(admin: Engine) -> None:
    granted: set[tuple[str, str]] = set()
    with admin.connect() as c:
        for table in _tables(admin):
            for priv in PRIVS:
                if c.execute(
                    text("SELECT has_table_privilege('rebate_ai_writer', :t, :p)"), {"t": table, "p": priv}
                ).scalar_one():
                    granted.add((table, priv))
    assert granted == AI_TABLE_GRANTS


def test_the_ai_role_may_update_only_the_result_columns(admin: Engine) -> None:
    writable: dict[str, set[str]] = {}
    with admin.connect() as c:
        for table, column in c.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
            )
        ):
            for priv in ("INSERT", "UPDATE"):
                if c.execute(
                    text("SELECT has_column_privilege('rebate_ai_writer', :t, :c, :p)"),
                    {"t": table, "c": column, "p": priv},
                ).scalar_one():
                    writable.setdefault(f"{table}:{priv}", set()).add(column)
    # ai_invocations: table-level INSERT shows up as column INSERT; nothing else is writable anywhere
    assert set(writable) == {"document_extractions:UPDATE", "ai_invocations:INSERT"}, writable.keys()
    assert writable["document_extractions:UPDATE"] == RESULT_COLUMNS


def test_the_app_role_can_queue_a_request_but_never_write_a_result(admin: Engine) -> None:
    with admin.connect() as c:

        def can(table: str, priv: str) -> bool:
            return bool(
                c.execute(
                    text("SELECT has_table_privilege('rebate_app', :t, :p)"), {"t": table, "p": priv}
                ).scalar_one()
            )

        assert can("document_extractions", "SELECT") and can("ai_invocations", "SELECT")
        for table in ("document_extractions", "ai_invocations"):
            for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not can(table, priv), (table, priv)
        queued = {
            col
            for (col,) in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'document_extractions'"
                )
            )
            if c.execute(
                text("SELECT has_column_privilege('rebate_app', 'document_extractions', :c, 'INSERT')"),
                {"c": col},
            ).scalar_one()
        }
        assert queued == {"tenant_id", "id", "file_id", "kind", "status", "created_by", "created_at"}
        failable = {
            col
            for (col,) in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'document_extractions'"
                )
            )
            if c.execute(
                text("SELECT has_column_privilege('rebate_app', 'document_extractions', :c, 'UPDATE')"),
                {"c": col},
            ).scalar_one()
        }
    assert failable == {"status", "error_code", "error_detail", "completed_at"}  # only to mark a row failed


@pytest.fixture
def queued(ws: Workspace) -> Iterator[tuple[UUID, UUID]]:
    """A queued extraction (job not run) of a tenant, through the API."""
    set_ai(ws, True)
    r = start(ws, golden_file(ws, SPA_884))
    assert r.status_code == 202, r.text
    yield UUID(ws.tenant_id), UUID(r.json()["id"])
    drain(ws.app)  # leave no queued job behind for the next test's worker loop


def _run(engine: Engine, tenant: UUID, sql: str, **params: object) -> int:
    with transaction(engine, tenant) as db:
        result = cast(CursorResult[Any], db.execute(text(sql), params))
        return result.rowcount


def test_the_ai_role_can_write_results_of_its_own_tenants_extraction(
    ai: Engine, queued: tuple[UUID, UUID]
) -> None:
    tenant, ex = queued
    assert (
        _run(
            ai,
            tenant,
            "UPDATE document_extractions SET status = 'processing', model = 'm' WHERE id = :i",
            i=ex,
        )
        == 1
    )
    with transaction(ai, tenant) as db:
        assert (
            db.execute(text("SELECT status FROM document_extractions WHERE id = :i"), {"i": ex}).scalar_one()
            == "processing"
        )


def test_the_ai_role_cannot_touch_other_tables_or_identity_columns(
    ai: Engine, queued: tuple[UUID, UUID]
) -> None:
    tenant, _ex = queued
    denied = [
        "INSERT INTO agreement_drafts (tenant_id, id, source, payload, payload_schema_version, created_by) "
        "VALUES (:t, gen_random_uuid(), 'ai_extraction', '{}', 1, :u)",
        "UPDATE agreement_drafts SET status = 'approved'",
        "UPDATE agreement_drafts SET payload = '{}'::jsonb",
        "INSERT INTO agreement_draft_revisions (tenant_id, id, draft_id, revision, payload, edited_by) "
        "VALUES (:t, gen_random_uuid(), gen_random_uuid(), 1, '{}', :u)",
        "DELETE FROM agreement_versions",
        "SELECT count(*) FROM agreement_versions",
        "SELECT count(*) FROM agreement_drafts",
        "SELECT count(*) FROM customers",
        "SELECT count(*) FROM products",
        "SELECT count(*) FROM users",
        "SELECT count(*) FROM tenant_settings",
        "SELECT count(*) FROM audit_log",
        "SELECT count(*) FROM jobs",
        "UPDATE tenant_settings SET ai_processing_enabled = true",
        "SELECT count(*) FROM file_objects",
        "UPDATE file_objects SET scan_status = 'clean'",
        "DELETE FROM file_objects",
        "DELETE FROM document_extractions",
        "TRUNCATE document_extractions",
        "UPDATE document_extractions SET file_id = gen_random_uuid()",
        "UPDATE document_extractions SET created_by = :u",
        "UPDATE document_extractions SET tenant_id = gen_random_uuid()",
        "INSERT INTO document_extractions (tenant_id, id, file_id, kind, created_by) "
        "VALUES (:t, gen_random_uuid(), gen_random_uuid(), 'agreement', :u)",
        "UPDATE ai_invocations SET cost_usd = 0",
        "DELETE FROM ai_invocations",
    ]
    for sql in denied:
        with pytest.raises(DBAPIError) as exc, transaction(ai, tenant) as db:
            db.execute(text(sql), {"t": tenant, "u": tenant})
        assert "permission denied" in str(exc.value), sql
    with pytest.raises(DBAPIError) as exc, transaction(ai, tenant) as db:
        db.execute(text("SELECT fn_user_memberships(gen_random_uuid())"))
    assert "permission denied" in str(exc.value)


def test_row_level_security_applies_to_the_ai_role(ai: Engine, queued: tuple[UUID, UUID]) -> None:
    tenant, ex = queued
    other = UUID(int=1)
    assert _run(ai, other, "UPDATE document_extractions SET status = 'failed' WHERE id = :i", i=ex) == 0
    with transaction(ai, other) as db:
        assert db.execute(text("SELECT count(*) FROM document_extractions")).scalar_one() == 0
    with transaction(ai, None) as db:  # no tenant context at all: fail closed
        assert db.execute(text("SELECT count(*) FROM document_extractions")).scalar_one() == 0
    with (
        pytest.raises(DBAPIError),
        transaction(ai, other) as db,
    ):  # cannot write a ledger row for another tenant
        db.execute(
            text(
                "INSERT INTO ai_invocations (tenant_id, id, purpose, provider, model, prompt_version, status) "
                "VALUES (:t, gen_random_uuid(), 'mapping', 'p', 'm', 'v', 'ok')"
            ),
            {"t": tenant},
        )


def test_a_finished_extraction_is_final(ai: Engine, queued: tuple[UUID, UUID]) -> None:
    tenant, ex = queued
    _run(
        ai,
        tenant,
        "UPDATE document_extractions SET status = 'failed', error_code = 'x', completed_at = now() WHERE id = :i",
        i=ex,
    )
    with pytest.raises(DBAPIError) as exc, transaction(ai, tenant) as db:
        db.execute(text("UPDATE document_extractions SET error_code = 'y' WHERE id = :i"), {"i": ex})
    assert "final" in str(exc.value)


def test_the_app_role_cannot_forge_results(migrated_db: DbUrls, queued: tuple[UUID, UUID]) -> None:
    tenant, _ex = queued
    app = make_engine(migrated_db.app)
    try:
        for sql in (
            "UPDATE document_extractions SET status = 'completed', result = '{}'::jsonb, completed_at = now()",
            "INSERT INTO document_extractions (tenant_id, id, file_id, kind, status, created_by, result) "
            "VALUES (:t, gen_random_uuid(), gen_random_uuid(), 'agreement', 'completed', :t, '{}')",
            "INSERT INTO ai_invocations (tenant_id, id, purpose, provider, model, prompt_version, status) "
            "VALUES (:t, gen_random_uuid(), 'mapping', 'p', 'm', 'v', 'ok')",
            "DELETE FROM document_extractions",
        ):
            with pytest.raises(DBAPIError) as exc, transaction(app, tenant) as db:
                db.execute(text(sql), {"t": tenant})
            assert "permission denied" in str(exc.value), sql
    finally:
        app.dispose()


def test_only_agreement_files_can_be_extracted(ws: Workspace) -> None:
    set_ai(ws, True)
    fid = clean_file(ws, "x.csv", b"a,b\n1,2\n")  # kind 'import'
    r = start(ws, fid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


def test_the_app_role_can_only_fail_an_unfinished_row(migrated_db: DbUrls, queued: tuple[UUID, UUID]) -> None:
    tenant, ex = queued
    app = make_engine(migrated_db.app)
    try:
        with (
            pytest.raises(DBAPIError) as exc,
            transaction(app, tenant) as db,
        ):  # the trigger, not only the grants
            db.execute(text("UPDATE document_extractions SET status = 'processing' WHERE id = :i"), {"i": ex})
        assert "may only mark an unfinished extraction failed" in str(exc.value)
        assert (
            _run(
                app,
                tenant,
                "UPDATE document_extractions SET status = 'failed', error_code = 'x', completed_at = now() WHERE id = :i",
                i=ex,
            )
            == 1
        )
        with pytest.raises(DBAPIError) as exc, transaction(app, tenant) as db:  # and a failed row is final
            db.execute(text("UPDATE document_extractions SET error_code = 'y' WHERE id = :i"), {"i": ex})
        assert "final" in str(exc.value)
    finally:
        app.dispose()
