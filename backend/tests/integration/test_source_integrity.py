"""Source-table invariants in the database (DATABASE_SCHEMA.md §5, ADR-024): natural key includes doc_type,
currency equals the workspace currency, retirement points at the next current version of the same key, and the
app role may update only the versioning columns. Raw SQL runs as ``rebate_app`` (the API role)."""

from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import Workspace, clean_file, run_import
from tests.isolation.test_db_rls import as_app

HEADER = (
    "invoice_no,line_no,invoice_date,ship_date,customer_code,customer_name,branch,sku,description,qty,uom,"
    "unit_price,unit_cost,ext_amount,spa_ref,project_ref,doc_type,orig_invoice_no,orig_line_no"
)
INVOICE = (
    "1001,1,2026-06-10,2026-06-10,ALDER,Alder,BR01,BRK-3P-100A,Breaker,10,EA,88.00,100.00,880.00,,,INV,,"
)
CREDIT = (
    "1001,1,2026-06-20,2026-06-20,ALDER,Alder,BR01,BRK-3P-100A,Breaker,2,EA,88.00,100.00,176.00,,,CN,1001,1"
)


def _lines(engine: Engine, tenant: str) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT id, doc_type, document_number, line_number, version, is_current, quantity "
                    "FROM sales_document_lines WHERE tenant_id = :t ORDER BY doc_type"
                ),
                {"t": tenant},
            ).mappings()
        )


def test_invoice_and_credit_note_with_the_same_number_both_stay_current(
    ws: Workspace, admin_engine: Engine
) -> None:
    first = run_import(ws, clean_file(ws, "inv.csv", f"{HEADER}\n{INVOICE}\n".encode()), "sales")
    assert first["counts"]["inserted"] == 1
    second = run_import(ws, clean_file(ws, "cn.csv", f"{HEADER}\n{CREDIT}\n".encode()), "sales")
    assert second["status"] == "completed" and second["counts"]["inserted"] == 1, second
    rows = _lines(admin_engine, ws.tenant_id)
    assert [(r["doc_type"], r["version"], r["is_current"]) for r in rows] == [
        ("credit_note", 1, True),
        ("invoice", 1, True),
    ]
    assert rows[0]["quantity"] < 0 < rows[1]["quantity"]  # BUSINESS_LOGIC §7 sign
    # both in one file: two keys, not a duplicate-key conflict
    both = run_import(ws, clean_file(ws, "both.csv", f"{HEADER}\n{INVOICE}\n{CREDIT}\n".encode()), "sales")
    assert both["counts"]["unchanged"] == 2 and both["counts"]["rejected"] == 0, both


def _error(conn: Connection, sql: str, params: dict[str, Any]) -> str:
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(sql), params)
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # fire the deferred checks now
    except DBAPIError as exc:
        savepoint.rollback()
        return str(exc.orig)
    savepoint.rollback()
    return ""


COPY_DOC = (
    "INSERT INTO sales_documents SELECT (jsonb_populate_record(NULL::sales_documents, to_jsonb(d) || "
    "jsonb_build_object('id', gen_random_uuid(), 'document_number', CAST(:n AS text), 'currency_code', CAST(:c AS text)))).* "
    "FROM sales_documents d WHERE tenant_id = :t LIMIT 1"
)


def test_currency_must_be_the_workspace_currency(ws: Workspace, app_engine: Engine) -> None:
    run_import(ws, clean_file(ws, "inv.csv", f"{HEADER}\n{INVOICE}\n".encode()), "sales")
    with as_app(app_engine, ws.tenant_id) as conn:
        eur = _error(conn, COPY_DOC, {"t": ws.tenant_id, "n": "X-EUR", "c": "EUR"})
        usd = _error(conn, COPY_DOC, {"t": ws.tenant_id, "n": "X-USD", "c": "USD"})
    assert "not the workspace currency USD" in eur, eur
    assert usd == "", usd


def test_retirement_must_point_at_the_next_current_version_of_the_same_key(
    ws: Workspace, app_engine: Engine
) -> None:
    body = f"{HEADER}\n{INVOICE}\n" + INVOICE.replace("1001,1,", "1002,1,") + "\n"
    run_import(ws, clean_file(ws, "two.csv", body.encode()), "sales")
    changed = INVOICE.replace(",10,EA,88.00,100.00,880.00,", ",11,EA,88.00,100.00,968.00,")
    v2 = run_import(ws, clean_file(ws, "v2.csv", f"{HEADER}\n{changed}\n".encode()), "sales")
    assert v2["counts"]["changed"] == 1, v2
    with as_app(app_engine, ws.tenant_id) as conn:
        ids: dict[str, Any] = dict(
            conn.execute(
                text("SELECT document_number, id FROM sales_document_lines WHERE is_current ORDER BY 1")
            )
            .tuples()
            .all()
        )
        v1 = conn.execute(
            text("SELECT id FROM sales_document_lines WHERE document_number = '1001' AND version = 1")
        ).scalar_one()
        retire = "UPDATE sales_document_lines SET is_current = false, superseded_by_id = :new WHERE id = :old"
        own = _error(conn, retire, {"old": ids["1001"], "new": ids["1001"]})
        other_key = _error(conn, retire, {"old": ids["1001"], "new": ids["1002"]})
        backwards = _error(conn, retire, {"old": ids["1001"], "new": v1})  # v2 -> v1 (not next, not current)
        missing = _error(conn, retire, {"old": ids["1001"], "new": "00000000-0000-7000-8000-000000000000"})
        quantity = _error(
            conn, "UPDATE sales_document_lines SET quantity = 1 WHERE id = :i", {"i": ids["1001"]}
        )
    assert "cannot supersede itself" in own, own
    assert "different natural key (document_number)" in other_key, other_key
    assert "successor must be the current version 3" in backwards, backwards
    assert "does not exist" in missing or "fk_sales_document_lines_superseded_by" in missing, missing
    assert "permission denied" in quantity, quantity  # column-level UPDATE grant


@pytest.mark.parametrize(
    "table", ["sales_documents", "sales_document_lines", "purchase_documents", "purchase_document_lines"]
)
def test_app_role_may_update_only_the_versioning_columns(admin_engine: Engine, table: str) -> None:
    with admin_engine.connect() as conn:
        cols = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'rebate_app' AND table_name = :t AND privilege_type = 'UPDATE'"
                ),
                {"t": table},
            ).scalars()
        )
        table_level = conn.execute(
            text("SELECT has_table_privilege('rebate_app', :t, 'UPDATE')"), {"t": table}
        ).scalar_one()
    assert cols == {"is_current", "superseded_by_id"} and table_level is False
