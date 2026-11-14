"""DB-level isolation attacks, connected as rebate_app (kickoff report §6.2, D1-D13).

Each D1-D8/D12 test detects a leak with an assertion message starting with ISOLATION-BREACH. With RLS
disabled (negative control) those assertions fail; D9/D10/D13 guard grants and do not depend on RLS.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from rebate.core.ids import new_id
from tests.conftest import DbUrls
from tests.isolation.conftest import TENANT_TABLES, World, breach


@contextmanager
def as_app(engine: Engine, tenant: UUID | str | None) -> Iterator[Connection]:
    """One rebate_app transaction with ``app.tenant_id`` set (or unset), always rolled back."""
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            if tenant is not None:
                conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
            yield conn
        finally:
            tx.rollback()


def _tenant_values(conn: Connection, table: str) -> set[str]:
    column = TENANT_TABLES[table]
    return {str(v) for v in conn.execute(text(f"SELECT {column} FROM {table}")).scalars()}


@pytest.mark.parametrize("table", sorted(TENANT_TABLES))
def test_d1_select_all_returns_only_own_tenant(app_engine: Engine, world: World, table: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        seen = _tenant_values(conn, table)
    assert str(world.a.tenant_id) in seen, f"{table}: fixture rows for A must be visible to A"
    assert seen == {str(world.a.tenant_id)}, breach(
        f"{table}: A sees rows of {seen - {str(world.a.tenant_id)}}"
    )


def test_d2_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = conn.execute(
            text("SELECT id FROM memberships WHERE id = ANY(:ids)"), {"ids": world.b.membership_ids}
        ).all()
        inv = conn.execute(
            text("SELECT id FROM invitations WHERE id = ANY(:ids)"), {"ids": world.b.invitation_ids}
        ).all()
        tenant = conn.execute(text("SELECT id FROM tenants WHERE id = :b"), {"b": world.b.tenant_id}).all()
        files = conn.execute(
            text("SELECT id FROM file_objects WHERE id = ANY(:ids)"), {"ids": world.b.file_ids}
        ).all()
    assert rows == [] and inv == [] and tenant == [] and files == [], breach("A reads B's rows by id")


def test_d3_update_foreign_row_affects_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        n = conn.execute(
            text("UPDATE memberships SET role = 'viewer' WHERE id = ANY(:ids)"),
            {"ids": world.b.membership_ids},
        ).rowcount
        s = conn.execute(
            text("UPDATE tenant_settings SET mfa_required = false WHERE tenant_id = :b"),
            {"b": world.b.tenant_id},
        ).rowcount
        f = conn.execute(
            text("UPDATE file_objects SET scan_detail = 'x' WHERE id = ANY(:ids)"), {"ids": world.b.file_ids}
        ).rowcount
    assert n == 0 and s == 0 and f == 0, breach(
        f"A updated {n} membership(s), {s} settings row(s) and {f} file(s) of B"
    )


def test_d4_delete_foreign_row_affects_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        n = conn.execute(
            text("DELETE FROM invitations WHERE id = ANY(:ids)"), {"ids": world.b.invitation_ids}
        ).rowcount
        k = conn.execute(
            text("DELETE FROM idempotency_keys WHERE tenant_id = :b"), {"b": world.b.tenant_id}
        ).rowcount
    assert n == 0 and k == 0, breach(f"A deleted {n} invitation(s) and {k} idempotency key(s) of B")


def _insert_rejected(conn: Connection, sql: str, params: dict[str, object]) -> bool:
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(sql), params)
    except DBAPIError as exc:
        savepoint.rollback()
        assert "row-level security" in str(exc.orig), f"unexpected error: {exc.orig}"
        return True
    savepoint.rollback()
    return False


def test_d5_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World) -> None:
    b = world.b.tenant_id
    with as_app(app_engine, world.a.tenant_id) as conn:
        rejected = {
            "memberships": _insert_rejected(
                conn,
                "INSERT INTO memberships (tenant_id, id, user_id, role) VALUES (:t, :id, :u, 'admin')",
                {"t": b, "id": new_id(), "u": world.a.admin_user_id},
            ),
            "audit_log": _insert_rejected(
                conn,
                "INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type) "
                "VALUES (:t, now(), 'system', 'probe.forged', 'probe')",
                {"t": b},
            ),
            "file_objects": _insert_rejected(
                conn,
                "INSERT INTO file_objects (tenant_id, id, kind, original_name, storage_key, size_bytes, "
                "sha256, detected_mime, uploaded_by) VALUES (:t, :id, 'attachment', 'x.csv', :k, 1, "
                "repeat('a', 64), 'text/csv', :u)",
                {"t": b, "id": (fid := new_id()), "k": f"quarantine/{b}/{fid}", "u": world.a.admin_user_id},
            ),
            "tenants": _insert_rejected(
                conn,
                "INSERT INTO tenants (id, name, slug) VALUES (:id, 'x', :s)",
                {"id": new_id(), "s": "iso-x"},
            ),
        }
    assert all(rejected.values()), breach(f"inserts into other tenants accepted: {rejected}")


def test_d6_moving_a_row_to_another_tenant_is_rejected(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        savepoint = conn.begin_nested()
        try:
            conn.execute(
                text("UPDATE memberships SET tenant_id = :b WHERE id = :id"),
                {"b": world.b.tenant_id, "id": world.a.membership_ids[0]},
            )
            moved = True
        except DBAPIError as exc:
            assert "row-level security" in str(exc.orig), f"unexpected error: {exc.orig}"
            moved = False
        savepoint.rollback()
    assert not moved, breach("A moved its membership into tenant B")


@pytest.mark.parametrize("setting", [None, ""], ids=["unset", "empty"])
def test_d7_no_tenant_context_sees_nothing(app_engine: Engine, world: World, setting: str | None) -> None:
    with as_app(app_engine, setting) as conn:
        leaked = {table: _tenant_values(conn, table) for table in TENANT_TABLES}
    assert not any(leaked.values()), breach(f"rows visible without tenant context: {leaked}")


def test_d8_unknown_tenant_sees_nothing(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, new_id()) as conn:
        leaked = {table: _tenant_values(conn, table) for table in TENANT_TABLES}
    assert not any(leaked.values()), breach(f"rows visible to an unknown tenant: {leaked}")


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY tenant_isolation ON memberships",
        "CREATE POLICY open_up ON memberships USING (true)",
        "SET ROLE rebate_owner",
        "SET ROLE rebate_lookup",
        "UPDATE audit_log SET action = 'x.y'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
        "UPDATE audit_seals SET seal_hash = repeat('b', 64)",
        "DELETE FROM audit_seals",
        "TRUNCATE audit_seals",
    ],
)
def test_d9_app_role_cannot_bypass_rls(app_engine: Engine, world: World, statement: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn, pytest.raises(DBAPIError) as err:
        for part in statement.split("; "):
            conn.execute(text(part))
    message = str(err.value.orig).lower()
    assert any(s in message for s in ("must be owner", "permission denied", "append-only")), message


def test_d9_row_security_off_errors_instead_of_bypassing(app_engine: Engine, world: World) -> None:
    """With row_security=off, a role subject to RLS gets an error instead of unfiltered rows."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        conn.execute(text("SELECT set_config('row_security', 'off', true)"))
        savepoint = conn.begin_nested()
        try:
            rows = conn.execute(text("SELECT tenant_id FROM memberships")).scalars().all()
        except DBAPIError as exc:
            savepoint.rollback()
            assert "row-level security" in str(exc.orig), str(exc.orig)
            return
        savepoint.rollback()
    raise AssertionError(breach(f"row_security=off returned {len(rows)} row(s) unfiltered"))


def test_d10_membership_lookup_returns_only_that_users_rows(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = (
            conn.execute(text("SELECT * FROM fn_user_memberships(:u)"), {"u": world.m_user_id})
            .mappings()
            .all()
        )
        a_admin = (
            conn.execute(text("SELECT tenant_id FROM fn_user_memberships(:u)"), {"u": world.a.admin_user_id})
            .scalars()
            .all()
        )
    assert {str(r["tenant_id"]) for r in rows} == {str(world.a.tenant_id), str(world.b.tenant_id)}
    assert set(rows[0].keys()) == {"tenant_id", "tenant_name", "role", "status"}
    assert [str(t) for t in a_admin] == [str(world.a.tenant_id)]


def test_d10_invitation_lookup_exposes_only_ids(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, None) as conn:
        row = (
            conn.execute(
                text("SELECT * FROM fn_invitation_by_token(:h)"),
                {"h": f"iso-token-{world.b.invitation_ids[0].hex}"},
            )
            .mappings()
            .one()
        )
        none = conn.execute(text("SELECT * FROM fn_invitation_by_token('nope')")).all()
    assert set(row.keys()) == {"tenant_id", "invitation_id"}
    assert row["invitation_id"] == world.b.invitation_ids[0] and none == []


def test_d12_force_rls_applies_to_the_owner(owner_engine: Engine, world: World) -> None:
    with owner_engine.connect() as conn:
        leaked = {table: _tenant_values(conn, table) for table in TENANT_TABLES}
        conn.rollback()
    assert not any(leaked.values()), breach(f"table owner sees rows without tenant context: {leaked}")


@pytest.mark.parametrize("role", ["rebate_readonly", "rebate_ai_writer"])
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM fn_user_memberships(gen_random_uuid())",
        "SELECT * FROM fn_invitation_by_token('x')",
        "SELECT count(*) FROM users",
        "SELECT count(*) FROM sessions",
        "SELECT count(*) FROM user_tokens",
        "SELECT count(*) FROM mfa_recovery_codes",
        "SELECT count(*) FROM pending_registrations",
        "SELECT count(*) FROM rate_limit_counters",
        "SELECT count(*) FROM jobs",
        "SELECT count(*) FROM password_reset_requests",
    ],
)
def test_d13_other_roles_cannot_reach_lookups_or_credentials(
    admin_engine: Engine, role: str, statement: str
) -> None:
    with admin_engine.connect() as conn:
        try:
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            with pytest.raises(DBAPIError) as err:
                conn.execute(text(statement))
            assert "permission denied" in str(err.value.orig)
        finally:
            conn.rollback()


def test_d13_idempotency_key_reuse_does_not_cross_tenants(app_engine: Engine, world: World) -> None:
    """The same key value exists in A and B (fixture); A can only ever read its own response."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        bodies = (
            conn.execute(text("SELECT response_body->>'tenant' FROM idempotency_keys WHERE key = 'same-key'"))
            .scalars()
            .all()
        )
    assert bodies == [str(world.a.tenant_id)], breach(f"idempotency replay across tenants: {bodies}")


def test_d13_owner_can_run_the_operator_lookup(owner_engine: Engine, world: World) -> None:
    """Runbook dependency: rebate_owner keeps EXECUTE after ownership moved to rebate_lookup."""
    with owner_engine.connect() as conn:
        sql = text("SELECT tenant_id FROM fn_user_memberships(:u)")
        rows = conn.execute(sql, {"u": world.m_user_id}).all()
        conn.rollback()
    assert len(rows) == 2


# ---- S3 tables (master data, import pipeline, source lines) -----------------------------------------
# table -> (column identifying the fixture row, key into TenantData.s3)
S3_ROWS: dict[str, tuple[str, str]] = {
    "manufacturers": ("id", "mfr"),
    "manufacturer_settings": ("manufacturer_id", "mfr"),
    "reason_code_mappings": ("id", "rcm"),
    "customers": ("id", "cust"),
    "customer_aliases": ("id", "calias"),
    "branches": ("id", "branch"),
    "products": ("id", "prod"),
    "product_aliases": ("id", "palias"),
    "uom_conversions": ("id", "uom"),
    "unmapped_entities": ("id", "unm"),
    "mapping_templates": ("id", "tpl"),
    "import_batches": ("id", "batch"),
    "import_staged_rows": ("import_batch_id", "batch"),
    "import_row_errors": ("id", "err"),
    "sales_documents": ("id", "sdoc"),
    "sales_document_lines": ("id", "sline"),
    "purchase_documents": ("id", "pdoc"),
    "purchase_document_lines": ("id", "pline"),
}
# Append-only/versioned: no DELETE grant and UPDATE granted only on is_current/superseded_by_id, so a tenant
# change is refused before RLS (both covered in tests/integration/test_masterdata.py and test_d9_s3_source_tables_*).
S3_SOURCE = ("sales_documents", "sales_document_lines", "purchase_documents", "purchase_document_lines")
S3_MUTABLE = sorted(set(S3_ROWS) - set(S3_SOURCE))


# ---- S4 tables (agreements) -----------------------------------------------------------------------------
S4_ROWS: dict[str, tuple[str, str]] = {
    "agreements": ("id", "agr"),
    "agreement_drafts": ("id", "draft"),
    "agreement_draft_revisions": ("id", "rev"),
    "agreement_versions": ("id", "ver"),
    "agreement_version_items": ("id", "item"),
    "agreement_version_customers": ("version_id", "ver"),
    "agreement_version_tiers": ("id", "tier"),
}
# UPDATE granted to the app role: fully (agreements, drafts) or on the status columns only (versions).
S4_UPDATABLE = {
    "agreements": "tenant_id = tenant_id",
    "agreement_drafts": "tenant_id = tenant_id",
    "agreement_versions": "status = status",
}
S4_MOVABLE = ("agreements", "agreement_drafts")

# ---- S5 tables (calculation runs) -----------------------------------------------------------------------
S5_ROWS: dict[str, tuple[str, str]] = {
    "calculation_runs": ("id", "run"),
    "calculation_results": ("id", "res"),
    "rebate_accrual_entries": ("id", "acc"),
    "claim_adjustments": ("id", "adj"),
}
# UPDATE granted to the app role on bookkeeping columns only (results and ledger entries are append-only).
S5_UPDATABLE = {"calculation_runs": "status = status", "claim_adjustments": "status = status"}


# ---- S6 tables (AI extraction): attacked as rebate_app and as the restricted rebate_ai_writer ------------
S6_ROWS: dict[str, tuple[str, str]] = {
    "document_extractions": ("id", "extraction"),
    "ai_invocations": ("id", "invocation"),
}


# ---- S7 tables (claims) ---------------------------------------------------------------------------------
S7_ROWS: dict[str, tuple[str, str]] = {
    "claim_export_templates": ("id", "tpl"),
    "claim_batches": ("id", "batch"),
    "claim_lines": ("id", "line"),
}
S7_UPDATABLE = {
    "claim_export_templates": "name = name",
    "claim_batches": "status = status",
    "claim_lines": "released_at = released_at",
}


# ---- S8 tables (remittances, reconciliation) ------------------------------------------------------------
S8_ROWS: dict[str, tuple[str, str]] = {
    "remittance_documents": ("id", "doc"),
    "remittance_lines": ("id", "rline"),
    "remittance_extraction_staging": ("id", "stg"),
    "reconciliation_matches": ("id", "match"),
}
S8_UPDATABLE = {
    "remittance_documents": "status = status",
    "remittance_extraction_staging": "corrected = corrected",
    "reconciliation_matches": "status = status",
    "remittance_lines": "category = category",
}


# ---- S9 tables (recovery cases) --------------------------------------------------------------------------
S9_ROWS: dict[str, tuple[str, str]] = {
    "variance_cases": ("id", "case"),
    "variance_case_events": ("id", "event"),
    "case_attachments": ("id", "attachment"),
    "notifications": ("id", "notification"),
}
S9_UPDATABLE = {  # the columns the app role may update at all; events and attachments are append-only
    "variance_cases": "status = status",
    "notifications": "read_at = now()",
}


# ---- S10 tables (reporting) -------------------------------------------------------------------------------
S10_ROWS: dict[str, tuple[str, str]] = {"summary_claims_monthly": ("id", "summary")}


def test_every_tenant_isolated_table_is_in_the_isolation_suite(admin_engine: Engine) -> None:
    """Every table carrying the tenant_isolation policy is attacked by D1/D7/D8/D12, and every S3 one by the
    S3 D-series below (policies survive the negative control's DISABLE ROW LEVEL SECURITY)."""
    with admin_engine.connect() as conn:
        policed = set(
            conn.execute(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")
            ).scalars()
        )
    s2 = {
        "tenants",
        "tenant_settings",
        "memberships",
        "invitations",
        "audit_log",
        "audit_seals",
        "idempotency_keys",
        "file_objects",
    }
    assert policed == set(TENANT_TABLES), policed ^ set(TENANT_TABLES)
    everything = (
        set(S3_ROWS)
        | set(S4_ROWS)
        | set(S5_ROWS)
        | set(S6_ROWS)
        | set(S7_ROWS)
        | set(S8_ROWS)
        | set(S9_ROWS)
        | set(S10_ROWS)
    )
    assert policed - s2 == everything, (policed - s2) ^ everything


def _outcome(conn: Connection, sql: str, params: dict[str, object]) -> str:
    """'rls' if row-level security rejected it, 'n=<rows>' if it ran, else the other error (= row was reachable)."""
    savepoint = conn.begin_nested()
    try:
        n = conn.execute(text(sql), params).rowcount
    except DBAPIError as exc:
        savepoint.rollback()
        return "rls" if "row-level security" in str(exc.orig) else f"error: {str(exc.orig).splitlines()[0]}"
    savepoint.rollback()
    return f"n={n}"


@pytest.mark.parametrize("table", sorted(S3_ROWS))
def test_d2_s3_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S3_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s3[key]}
        ).all()
    assert rows == [], breach(f"A reads B's {table} row by id")


@pytest.mark.parametrize("table", sorted(S3_ROWS))
def test_d3_s3_update_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S3_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        # source tables: the app role may only update the versioning columns (column-level grant)
        target = "is_current = is_current" if table in S3_SOURCE else "tenant_id = tenant_id"
        got = _outcome(conn, f"UPDATE {table} SET {target} WHERE {column} = :id", {"id": world.b.s3[key]})
    assert got == "n=0", breach(f"A updated B's {table} row ({got})")


@pytest.mark.parametrize("table", S3_MUTABLE)
def test_d4_s3_delete_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S3_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.b.s3[key]})
    assert got == "n=0", breach(f"A deleted B's {table} row ({got})")


@pytest.mark.parametrize("table", sorted(S3_ROWS))
def test_d5_s3_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World, table: str) -> None:
    """Copies A's own fixture row with tenant_id = B (and a fresh id); RLS WITH CHECK must reject it."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(x) || "
            f"jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()))).* "
            f"FROM {table} x WHERE tenant_id = :a",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"insert of a {table} row into tenant B was not rejected by RLS ({got})")


@pytest.mark.parametrize("table", S3_MUTABLE)
def test_d6_s3_moving_a_row_to_another_tenant_is_rejected(
    app_engine: Engine, world: World, table: str
) -> None:
    column, key = S3_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s3[key]},
        )
    assert got == "rls", breach(f"A moved its {table} row into tenant B ({got})")


@pytest.mark.parametrize("table", S3_SOURCE)
def test_d9_s3_source_tables_cannot_be_deleted_or_moved(app_engine: Engine, world: World, table: str) -> None:
    column, key = S3_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        deleted = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.a.s3[key]})
        moved = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s3[key]},
        )
    assert deleted.startswith("error: permission denied"), deleted
    assert moved.startswith("error: permission denied"), (
        moved
    )  # UPDATE granted on is_current/superseded_by_id only


@pytest.mark.parametrize("table", sorted(S4_ROWS))
def test_d2_s4_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S4_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s4[key]}
        ).all()
    assert rows == [], breach(f"A reads B's {table} row by id")


@pytest.mark.parametrize("table", sorted(S4_UPDATABLE))
def test_d3_s4_update_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S4_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn, f"UPDATE {table} SET {S4_UPDATABLE[table]} WHERE {column} = :id", {"id": world.b.s4[key]}
        )
    assert got == "n=0", breach(f"A updated B's {table} row ({got})")


@pytest.mark.parametrize("table", sorted(S4_ROWS))
def test_d5_s4_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World, table: str) -> None:
    """As the S3 D5, with an explicit column list: generated columns (valid_range) cannot be inserted."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        cols = ", ".join(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
                ),
                {"t": table},
            ).scalars()
        )
        got = _outcome(
            conn,
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::{table}, "
            f"(SELECT to_jsonb(x) || jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()) "
            f"FROM {table} x WHERE tenant_id = :a LIMIT 1))",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"insert of a {table} row into tenant B was not rejected by RLS ({got})")


@pytest.mark.parametrize("table", S4_MOVABLE)
def test_d6_s4_moving_a_row_to_another_tenant_is_rejected(
    app_engine: Engine, world: World, table: str
) -> None:
    column, key = S4_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s4[key]},
        )
    assert got == "rls", breach(f"A moved its {table} row into tenant B ({got})")


@pytest.mark.parametrize("table", sorted(S4_ROWS))
def test_d9_s4_no_delete_and_no_tenant_change_of_terms(app_engine: Engine, world: World, table: str) -> None:
    """No DELETE on any S4 table; versions, their terms and revisions cannot change tenant (no UPDATE grant on
    the column). Grants, so independent of RLS."""
    column, key = S4_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        deleted = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.a.s4[key]})
        moved = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s4[key]},
        )
    assert deleted.startswith("error: permission denied"), deleted
    if table not in S4_MOVABLE:
        assert moved.startswith("error: permission denied"), moved


@pytest.mark.parametrize("table", sorted(S5_ROWS))
def test_d2_s5_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S5_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s5[key]}
        ).all()
    assert rows == [], breach(f"A reads B's {table} row by id")


@pytest.mark.parametrize("table", sorted(S5_UPDATABLE))
def test_d3_s5_update_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S5_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn, f"UPDATE {table} SET {S5_UPDATABLE[table]} WHERE {column} = :id", {"id": world.b.s5[key]}
        )
    assert got == "n=0", breach(f"A updated B's {table} row ({got})")


@pytest.mark.parametrize("table", sorted(S5_ROWS))
def test_d5_s5_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World, table: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        cols = ", ".join(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
                ),
                {"t": table},
            ).scalars()
        )
        got = _outcome(
            conn,
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::{table}, "
            f"(SELECT to_jsonb(x) || jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()) "
            f"FROM {table} x WHERE tenant_id = :a LIMIT 1))",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"insert of a {table} row into tenant B was not rejected by RLS ({got})")


@pytest.mark.parametrize("table", sorted(S5_ROWS))
def test_d9_s5_no_delete_and_no_tenant_change(app_engine: Engine, world: World, table: str) -> None:
    """Append-only history: no DELETE, and the tenant column is not updatable (grants, independent of RLS)."""
    column, key = S5_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        deleted = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.a.s5[key]})
        moved = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s5[key]},
        )
    assert deleted.startswith("error: permission denied"), deleted
    assert moved.startswith("error: permission denied"), moved


@pytest.mark.parametrize("table", ["calculation_results", "rebate_accrual_entries"])
def test_s5_triggers_forbid_mutation_even_for_the_owner(
    admin_engine: Engine, world: World, table: str
) -> None:
    """The triggers (not only the revoked privileges) make the calculation history append-only."""
    column, key = S5_ROWS[table]
    for sql in (
        f"UPDATE {table} SET inputs_hash = 'x' WHERE {column} = :id",
        f"DELETE FROM {table} WHERE {column} = :id",
    ):
        with admin_engine.connect() as conn:
            got = _outcome(conn, sql, {"id": world.a.s5[key]})
        assert "append-only" in got, got


def test_s5_adjustment_approval_columns_move_only_with_the_status(admin_engine: Engine, world: World) -> None:
    key = world.a.s5["adj"]
    with admin_engine.connect() as conn:
        got = _outcome(
            conn,
            "UPDATE claim_adjustments SET approved_by = :u, approved_at = now() WHERE id = :id",
            {"u": world.a.admin_user_id, "id": key},
        )
        ok = _outcome(
            conn,
            "UPDATE claim_adjustments SET status = 'approved', approved_by = :u, approved_at = now() WHERE id = :id",
            {"u": world.a.admin_user_id, "id": key},
        )
    assert "approval columns change only with the status" in got, got
    assert ok == "n=1", ok


@pytest.fixture(scope="module")
def ai_engine(migrated_db: DbUrls) -> Iterator[Engine]:
    from tests.extraction.conftest import ai_url

    engine = create_engine(ai_url(migrated_db))
    yield engine
    engine.dispose()


@pytest.mark.parametrize("table", sorted(S6_ROWS))
@pytest.mark.parametrize("role", ["app", "ai"])
def test_d2_s6_select_by_foreign_id_returns_nothing(
    app_engine: Engine, ai_engine: Engine, world: World, table: str, role: str
) -> None:
    column, key = S6_ROWS[table]
    engine = app_engine if role == "app" else ai_engine
    with as_app(engine, world.a.tenant_id) as conn:
        own = conn.execute(text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.a.s6[key]}).all()
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s6[key]}
        ).all()
    assert own, f"{table}: fixture row for A must be visible to A as {role}"
    assert rows == [], breach(f"A reads B's {table} row by id as {role}")


def test_d3_s6_ai_role_cannot_update_a_foreign_extraction(ai_engine: Engine, world: World) -> None:
    with as_app(ai_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            "UPDATE document_extractions SET status = 'processing' WHERE id = :id",
            {"id": world.b.s6["extraction"]},
        )
    assert got == "n=0", breach(f"A (as the AI role) updated B's extraction ({got})")


def test_d5_s6_ai_role_cannot_write_a_ledger_row_into_another_tenant(ai_engine: Engine, world: World) -> None:
    with as_app(ai_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            "INSERT INTO ai_invocations (tenant_id, id, extraction_id, purpose, provider, model, prompt_version, "
            "status) VALUES (:b, gen_random_uuid(), NULL, 'mapping', 'p', 'm', 'v', 'ok')",
            {"b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"A (as the AI role) wrote a ledger row into tenant B ({got})")


def test_d7_s6_ai_role_without_tenant_context_sees_nothing(ai_engine: Engine, world: World) -> None:
    with as_app(ai_engine, None) as conn:
        counts = {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("document_extractions", "ai_invocations")
        }
    assert counts == {"document_extractions": 0, "ai_invocations": 0}, breach(str(counts))


@pytest.mark.parametrize("table", sorted(S6_ROWS))
def test_d9_s6_nobody_deletes_and_the_app_role_cannot_write_results(
    app_engine: Engine, ai_engine: Engine, world: World, table: str
) -> None:
    """Grants and triggers, independent of RLS: the ledger and the extraction rows are never deleted, the app
    role cannot update them, and the AI role cannot change identity columns."""
    column, key = S6_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        results = [
            _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.a.s6[key]}),
            _outcome(
                conn,
                f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
                {"b": world.b.tenant_id, "id": world.a.s6[key]},
            ),
        ]
    with as_app(ai_engine, world.a.tenant_id) as conn:
        results += [
            _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.a.s6[key]}),
            _outcome(
                conn,
                f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
                {"b": world.b.tenant_id, "id": world.a.s6[key]},
            ),
        ]
    assert all(r.startswith("error: permission denied") for r in results), results


# ---- S7 attacks ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(S7_ROWS))
def test_d2_s7_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S7_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        own = conn.execute(text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.a.s7[key]}).all()
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s7[key]}
        ).all()
    assert own, f"{table}: fixture row for A must be visible to A"
    assert rows == [], breach(f"A reads B's {table} row by id")


@pytest.mark.parametrize("table", sorted(S7_UPDATABLE))
def test_d3_s7_update_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S7_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn, f"UPDATE {table} SET {S7_UPDATABLE[table]} WHERE {column} = :id", {"id": world.b.s7[key]}
        )
    assert got == "n=0", breach(f"A updated B's {table} row ({got})")


@pytest.mark.parametrize("table", ["claim_export_templates", "claim_batches"])
def test_d5_s7_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World, table: str) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        cols = ", ".join(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
                ),
                {"t": table},
            ).scalars()
        )
        got = _outcome(
            conn,
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::{table}, "
            f"(SELECT to_jsonb(x) || jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()) "
            f"FROM {table} x WHERE tenant_id = :a LIMIT 1))",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"insert of a {table} row into tenant B was not rejected by RLS ({got})")


@pytest.mark.parametrize("table", sorted(S7_ROWS))
def test_d9_s7_nobody_deletes_and_the_tenant_column_is_pinned(
    app_engine: Engine, world: World, table: str
) -> None:
    """No DELETE for any S7 table (history), and for batches and lines no UPDATE on tenant_id (grants)."""
    column, key = S7_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        deleted = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.a.s7[key]})
        moved = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s7[key]},
        )
    assert deleted.startswith("error: permission denied"), deleted
    if table != "claim_export_templates":
        assert moved.startswith("error: permission denied"), moved


# ---- S8 attacks ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(S8_ROWS))
def test_d2_s8_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S8_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        own = conn.execute(text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.a.s8[key]}).all()
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s8[key]}
        ).all()
    assert own, f"{table}: fixture row for A must be visible to A"
    assert rows == [], breach(f"A reads B's {table} row by id")


@pytest.mark.parametrize("table", sorted(S8_UPDATABLE))
def test_d3_s8_update_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S8_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn, f"UPDATE {table} SET {S8_UPDATABLE[table]} WHERE {column} = :id", {"id": world.b.s8[key]}
        )
    assert got == "n=0", breach(f"A updated B's {table} row ({got})")


def test_s8_the_app_role_cannot_change_remittance_line_evidence(app_engine: Engine, world: World) -> None:
    """Only the three categorisation columns are granted; the money and identifiers of a line are evidence."""
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            "UPDATE remittance_lines SET amount_paid = 1 WHERE id = :id",
            {"id": world.a.s8["rline"]},
        )
    assert got.startswith("error: permission denied"), got


S8_INSERT_AS_NEW = {  # what a copy must change so that the table's own BEFORE INSERT trigger lets it reach the policy
    "remittance_documents": "'status', 'uploaded', 'accepted_by', NULL, 'accepted_at', NULL",
}


TRIGGER_GUARDED = {  # their BEFORE INSERT trigger reads a parent row as the caller, so no foreign insert reaches the policy
    "reconciliation_matches": "matches can be added only to an accepted remittance",
    "case_attachments": "only a clean-scanned attachment file can be attached",
}


@pytest.mark.parametrize("table", sorted(set(S8_ROWS) - set(TRIGGER_GUARDED)))
def test_d5_s8_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World, table: str) -> None:
    """The copy must get past the table's own triggers, so that only the row-level-security policy can refuse it
    (with RLS off it then fails on a foreign key instead, and the test reports a breach)."""
    extra = S8_INSERT_AS_NEW.get(table)
    with as_app(app_engine, world.a.tenant_id) as conn:
        cols = ", ".join(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
                ),
                {"t": table},
            ).scalars()
        )
        got = _outcome(
            conn,
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::{table}, "
            f"(SELECT to_jsonb(x) || jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()"
            f"{', ' + extra if extra else ''}) FROM {table} x WHERE tenant_id = :a LIMIT 1))",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"insert of a {table} row into tenant B was not rejected ({got})")


S8_DELETABLE = ("remittance_extraction_staging", "reconciliation_matches")


@pytest.mark.parametrize("table", sorted(set(S8_ROWS) - set(S8_DELETABLE)))
def test_d9_s8_documents_and_lines_are_never_deleted(app_engine: Engine, world: World, table: str) -> None:
    """No DELETE privilege on documents and lines (independent of RLS)."""
    column, key = S8_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.b.s8[key]})
    assert got.startswith("error: permission denied"), got


@pytest.mark.parametrize("table", sorted(set(S8_ROWS) - {"remittance_extraction_staging"}))
def test_d9_s8_the_tenant_column_cannot_be_updated(app_engine: Engine, world: World, table: str) -> None:
    """No UPDATE privilege on the tenant column (independent of RLS); staging carries it and is covered by D3."""
    column, key = S8_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s8[key]},
        )
    assert got.startswith("error: permission denied"), got


@pytest.mark.parametrize("table", sorted(S8_DELETABLE))
def test_d3_s8_delete_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    """Staging and matches carry DELETE for the own tenant only: a foreign row is invisible (RLS)."""
    column, key = S8_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.b.s8[key]})
    assert got == "n=0", breach(f"A deleted B's {table} row ({got})")


def test_d3_s8_staging_row_cannot_be_moved_to_another_tenant(app_engine: Engine, world: World) -> None:
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn,
            "UPDATE remittance_extraction_staging SET tenant_id = :b WHERE id = :id",
            {"b": world.b.tenant_id, "id": world.a.s8["stg"]},
        )
    assert got == "rls", breach(f"A moved a staging row into tenant B ({got})")


# ---- S9 attacks ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(S9_ROWS))
def test_d2_s9_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S9_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        own = conn.execute(text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.a.s9[key]}).all()
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s9[key]}
        ).all()
    assert len(own) == 1 and rows == [], breach(f"A read B's {table} row")


@pytest.mark.parametrize("table", sorted(S9_UPDATABLE))
def test_d3_s9_update_foreign_row_affects_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S9_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        got = _outcome(
            conn, f"UPDATE {table} SET {S9_UPDATABLE[table]} WHERE {column} = :id", {"id": world.b.s9[key]}
        )
    assert got == "n=0", breach(f"A updated B's {table} row ({got})")


@pytest.mark.parametrize("table", sorted(set(S9_ROWS) - set(TRIGGER_GUARDED)))
def test_d5_s9_insert_into_foreign_tenant_is_rejected(app_engine: Engine, world: World, table: str) -> None:
    extra = None
    with as_app(app_engine, world.a.tenant_id) as conn:
        cols = ", ".join(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
                ),
                {"t": table},
            ).scalars()
        )
        got = _outcome(
            conn,
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::{table}, "
            f"(SELECT to_jsonb(x) || jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()"
            f"{', ' + extra if extra else ''}) FROM {table} x WHERE tenant_id = :a LIMIT 1))",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert got == "rls", breach(f"insert of a {table} row into tenant B was not rejected ({got})")


@pytest.mark.parametrize("table", sorted(TRIGGER_GUARDED))
def test_d10_trigger_guarded_tables_refuse_a_copy_into_a_foreign_tenant(
    app_engine: Engine, world: World, table: str
) -> None:
    """Not an RLS test (it passes with RLS off too): the table's own BEFORE INSERT trigger looks up the parent row
    (remittance, file) as the caller and finds none in the other tenant, so the copy never reaches the policy."""
    cols_sql = (
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
    )
    with as_app(app_engine, world.a.tenant_id) as conn:
        cols = ", ".join(conn.execute(text(cols_sql), {"t": table}).scalars())
        got = _outcome(
            conn,
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::{table}, "
            f"(SELECT to_jsonb(x) || jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()) "
            f"FROM {table} x WHERE tenant_id = :a LIMIT 1))",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
    assert TRIGGER_GUARDED[table] in got, got


@pytest.mark.parametrize("table", sorted(S9_ROWS))
def test_d9_s9_nothing_is_deleted_and_the_tenant_column_cannot_move(
    app_engine: Engine, world: World, table: str
) -> None:
    """No S9 table grants DELETE to the app role; none lets the tenant column be updated (cases and notifications
    grant only their own columns; events and attachments no UPDATE at all)."""
    column, key = S9_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        foreign = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.b.s9[key]})
        moved = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s9[key]},
        )
    assert foreign.startswith("error: permission denied"), foreign
    assert moved.startswith("error: permission denied") or moved == "rls" or "are fixed" in moved, moved


# ---- S10 attacks (the summary table is rebuilt by the refresh job: the app role may update and delete its rows) ----


@pytest.mark.parametrize("table", sorted(S10_ROWS))
def test_d2_s10_select_by_foreign_id_returns_nothing(app_engine: Engine, world: World, table: str) -> None:
    column, key = S10_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        own = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.a.s10[key]}
        ).all()
        rows = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": world.b.s10[key]}
        ).all()
    assert len(own) == 1 and rows == [], breach(f"A read B's {table} row")


@pytest.mark.parametrize("table", sorted(S10_ROWS))
def test_d3_d4_s10_update_and_delete_of_a_foreign_row_affect_nothing(
    app_engine: Engine, world: World, table: str
) -> None:
    column, key = S10_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        updated = _outcome(
            conn, f"UPDATE {table} SET tenant_id = tenant_id WHERE {column} = :id", {"id": world.b.s10[key]}
        )
        deleted = _outcome(conn, f"DELETE FROM {table} WHERE {column} = :id", {"id": world.b.s10[key]})
    assert (updated, deleted) == ("n=0", "n=0"), breach(f"A changed B's {table} row ({updated}, {deleted})")


@pytest.mark.parametrize("table", sorted(S10_ROWS))
def test_d5_d6_s10_insert_or_move_into_a_foreign_tenant_is_rejected(
    app_engine: Engine, world: World, table: str
) -> None:
    column, key = S10_ROWS[table]
    with as_app(app_engine, world.a.tenant_id) as conn:
        inserted = _outcome(
            conn,
            f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(x) || "
            f"jsonb_build_object('tenant_id', CAST(:b AS uuid), 'id', gen_random_uuid()))).* "
            f"FROM {table} x WHERE tenant_id = :a",
            {"a": world.a.tenant_id, "b": world.b.tenant_id},
        )
        moved = _outcome(
            conn,
            f"UPDATE {table} SET tenant_id = :b WHERE {column} = :id",
            {"b": world.b.tenant_id, "id": world.a.s10[key]},
        )
    assert (inserted, moved) == ("rls", "rls"), breach(
        f"{table}: insert/move into tenant B ({inserted}, {moved})"
    )
