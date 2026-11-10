"""Isolation fixtures: two tenants (A, B) with rows in every tenant table, plus user M in both.

Seeded once per session through the superuser connection (tests only; bypasses RLS on purpose so the
seed itself cannot be filtered). Every attack then runs as ``rebate_app``, the role the API uses.

Assertions that detect a cross-tenant leak start with ``BREACH`` (see ``breach()``): the negative
control (infra/scripts/isolation_negative_control.py) requires exactly those assertion failures when
RLS is switched off, so a test that fails for any other reason does not count as "detected".
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from rebate.core.ids import new_id

BREACH = "ISOLATION-BREACH"

# Every tenant table and the column holding the tenant (tenants is keyed by its own id).
TENANT_TABLES: dict[str, str] = {
    "tenants": "id",
    "tenant_settings": "tenant_id",
    "memberships": "tenant_id",
    "invitations": "tenant_id",
    "audit_log": "tenant_id",
    "idempotency_keys": "tenant_id",
    "file_objects": "tenant_id",
    # S3 master data and import pipeline
    "manufacturers": "tenant_id",
    "manufacturer_settings": "tenant_id",
    "reason_code_mappings": "tenant_id",
    "customers": "tenant_id",
    "customer_aliases": "tenant_id",
    "branches": "tenant_id",
    "products": "tenant_id",
    "product_aliases": "tenant_id",
    "uom_conversions": "tenant_id",
    "unmapped_entities": "tenant_id",
    "mapping_templates": "tenant_id",
    "import_batches": "tenant_id",
    "import_staged_rows": "tenant_id",
    "import_row_errors": "tenant_id",
    "sales_documents": "tenant_id",
    "sales_document_lines": "tenant_id",
    "purchase_documents": "tenant_id",
    "purchase_document_lines": "tenant_id",
}


def breach(detail: str) -> str:
    return f"{BREACH}: {detail}"


@dataclass
class TenantData:
    tenant_id: UUID
    admin_user_id: UUID
    membership_ids: list[UUID] = field(default_factory=list)
    invitation_ids: list[UUID] = field(default_factory=list)
    file_ids: list[UUID] = field(default_factory=list)
    s3: dict[str, UUID] = field(default_factory=dict)  # one row id per S3 tenant table


@dataclass
class World:
    a: TenantData
    b: TenantData
    m_user_id: UUID  # member of both A (analyst) and B (admin)


def _user(conn: Connection, email: str) -> UUID:
    uid = new_id()
    conn.execute(
        text(
            "INSERT INTO users (id, email, password_hash, full_name, email_verified_at) "
            "VALUES (:id, :email, 'x', :name, now())"
        ),
        {"id": uid, "email": email, "name": email.split("@")[0]},
    )
    return uid


def _tenant(conn: Connection, name: str, admin_email: str) -> TenantData:
    tid = new_id()
    admin = _user(conn, admin_email)
    conn.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
        {"id": tid, "n": name, "s": f"iso-{tid.hex[-12:]}"},
    )
    conn.execute(text("INSERT INTO tenant_settings (tenant_id) VALUES (:t)"), {"t": tid})
    data = TenantData(tenant_id=tid, admin_user_id=admin)
    data.membership_ids.append(_membership(conn, tid, admin, "admin"))
    inv = new_id()
    conn.execute(
        text(
            "INSERT INTO invitations (tenant_id, id, email, role, token_hash, invited_by, expires_at) "
            "VALUES (:t, :id, :e, 'viewer', :h, :by, :exp)"
        ),
        {
            "t": tid,
            "id": inv,
            "e": f"invitee-{tid.hex[:8]}@example.test",
            "h": f"iso-token-{inv.hex}",
            "by": admin,
            "exp": datetime.now(UTC) + timedelta(days=7),
        },
    )
    data.invitation_ids.append(inv)
    fid = new_id()
    conn.execute(
        text(
            "INSERT INTO file_objects (tenant_id, id, kind, original_name, storage_key, size_bytes, sha256, "
            "detected_mime, uploaded_by) VALUES (:t, :id, 'import', 'iso.csv', :k, 10, :h, 'text/csv', :u)"
        ),
        {"t": tid, "id": fid, "k": f"quarantine/{tid}/{fid}", "h": fid.hex * 2, "u": admin},
    )
    data.file_ids.append(fid)
    data.s3 = _s3_rows(conn, tid, admin, fid)
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, occurred_at, actor_type, actor_user_id, action, "
            "entity_type, entity_id) VALUES (:t, now(), 'user', :u, 'tenant.created', 'tenant', :t)"
        ),
        {"t": tid, "u": admin},
    )
    conn.execute(
        text(
            "INSERT INTO idempotency_keys (tenant_id, key, endpoint, request_hash, response_status, "
            "response_body, expires_at) VALUES (:t, 'same-key', '/probe', 'h', 200, "
            "jsonb_build_object('tenant', CAST(:t AS text)), now() + interval '1 day')"
        ),
        {"t": tid},
    )
    return data


def _s3_rows(conn: Connection, t: UUID, user: UUID, file_id: UUID) -> dict[str, UUID]:
    """One row in every S3 tenant table (master data, import pipeline, source lines)."""
    ids = {k: new_id() for k in ("mfr", "rcm", "cust", "calias", "branch", "prod", "palias", "uom", "unm",
                                 "tpl", "batch", "err", "sdoc", "sline", "pdoc", "pline")}  # fmt: skip
    p = {"t": t, "u": user, "f": file_id, **ids}
    h = "a" * 64
    for sql in (
        "INSERT INTO manufacturers (tenant_id, id, name, code) VALUES (:t, :mfr, 'Iso Mfr', 'ISO')",
        "INSERT INTO manufacturer_settings (tenant_id, manufacturer_id) VALUES (:t, :mfr)",
        "INSERT INTO reason_code_mappings (tenant_id, id, manufacturer_id, external_code, reason_code) "
        "VALUES (:t, :rcm, :mfr, 'E1', 'unknown')",
        "INSERT INTO customers (tenant_id, id, name, code) VALUES (:t, :cust, 'Iso Customer', 'ISOC')",
        "INSERT INTO customer_aliases (tenant_id, id, customer_id, alias) VALUES (:t, :calias, :cust, 'ISO ALIAS')",
        "INSERT INTO branches (tenant_id, id, name, code) VALUES (:t, :branch, 'Iso Branch', 'ISOB')",
        "INSERT INTO products (tenant_id, id, sku, manufacturer_id, base_uom) VALUES (:t, :prod, 'ISO-SKU', :mfr, 'EA')",
        "INSERT INTO product_aliases (tenant_id, id, product_id, alias, alias_type) VALUES (:t, :palias, :prod, 'ISO-P', 'other')",
        "INSERT INTO uom_conversions (tenant_id, id, product_id, from_uom, to_uom, factor) VALUES (:t, :uom, :prod, 'RL', 'FT', 500)",
        "INSERT INTO mapping_templates (tenant_id, id, name, import_type, header_signature, mapping, created_by) "
        f"VALUES (:t, :tpl, 'iso', 'sales', '{h}', '{{}}', :u)",
        "INSERT INTO import_batches (tenant_id, id, import_type, file_id, source_system, status, created_by) "
        "VALUES (:t, :batch, 'sales', :f, 'ISO', 'awaiting_confirmation', :u)",
        "INSERT INTO unmapped_entities (tenant_id, id, entity_type, external_value, first_import_batch_id) "
        "VALUES (:t, :unm, 'product', 'ISO-UNKNOWN', :batch)",
        "INSERT INTO import_staged_rows (tenant_id, import_batch_id, row_number, raw) VALUES (:t, :batch, 2, '{}')",
        "INSERT INTO import_row_errors (tenant_id, id, import_batch_id, row_number, code, message) "
        "VALUES (:t, :err, :batch, 2, 'probe', 'probe')",
        "INSERT INTO sales_documents (tenant_id, id, import_batch_id, source_system, doc_type, document_number, "
        f"document_date, customer_id, currency_code, row_hash) VALUES (:t, :sdoc, :batch, 'ISO', 'invoice', 'INV-ISO', "
        f"'2026-06-01', :cust, 'USD', '{h}')",
        "INSERT INTO sales_document_lines (tenant_id, id, document_id, import_batch_id, source_row_number, source_system, "
        "doc_type, document_number, line_number, product_id, sku_raw, manufacturer_id, customer_id, quantity, uom, "
        "unit_price, extended_amount, currency_code, invoice_date, row_hash) VALUES (:t, :sline, :sdoc, :batch, 2, "
        f"'ISO', 'invoice', 'INV-ISO', 1, :prod, 'ISO-SKU', :mfr, :cust, 1, 'EA', 1, 1, 'USD', '2026-06-01', '{h}')",
        "INSERT INTO purchase_documents (tenant_id, id, import_batch_id, source_system, doc_type, document_number, "
        f"document_date, manufacturer_id, currency_code, row_hash) VALUES (:t, :pdoc, :batch, 'ISO', 'receipt', 'RCV-ISO', "
        f"'2026-06-01', :mfr, 'USD', '{h}')",
        "INSERT INTO purchase_document_lines (tenant_id, id, document_id, import_batch_id, source_row_number, "
        "source_system, doc_type, document_number, line_number, sku_raw, manufacturer_id, quantity, uom, unit_cost, "
        f"extended_amount, currency_code, document_date, row_hash) VALUES (:t, :pline, :pdoc, :batch, 2, 'ISO', "
        f"'receipt', 'RCV-ISO', 1, 'ISO-SKU', :mfr, 1, 'EA', 1, 1, 'USD', '2026-06-01', '{h}')",
    ):
        conn.execute(text(sql), p)
    return ids


def _membership(conn: Connection, tid: UUID, uid: UUID, role: str) -> UUID:
    mid = new_id()
    conn.execute(
        text("INSERT INTO memberships (tenant_id, id, user_id, role) VALUES (:t, :id, :u, :r)"),
        {"t": tid, "id": mid, "u": uid, "r": role},
    )
    return mid


@pytest.fixture(scope="session")
def world(admin_engine: Engine) -> World:
    suffix = new_id().hex[:8]
    with admin_engine.begin() as conn:
        a = _tenant(conn, "Tenant A (SYNTHETIC)", f"admin-a-{suffix}@example.test")
        b = _tenant(conn, "Tenant B (SYNTHETIC)", f"admin-b-{suffix}@example.test")
        m = _user(conn, f"member-m-{suffix}@example.test")
        a.membership_ids.append(_membership(conn, a.tenant_id, m, "analyst"))
        b.membership_ids.append(_membership(conn, b.tenant_id, m, "admin"))
    return World(a=a, b=b, m_user_id=m)


def tenant_ids_in(rows: Iterable[object]) -> set[str]:
    return {str(r) for r in rows}
