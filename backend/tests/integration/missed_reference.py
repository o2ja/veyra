"""The pre-S11 missed-claims SQL (``DISTINCT ON`` latest, recursive ancestor check), kept as the oracle that the
rewritten ``rebate.modules.claims.missed`` pieces return identical rows in identical order."""

from typing import Any

from sqlalchemy import Engine, text

from rebate.modules.claims import missed

LATEST_REFERENCE = """
latest AS (
    SELECT DISTINCT ON (r.sales_document_line_id)
        r.id, r.tenant_id, r.run_id, r.sales_document_line_id, r.document_number, r.line_number, r.invoice_date,
        r.manufacturer_id, r.agreement_id, r.agreement_version_id, r.status, r.exception_code,
        r.eligible_quantity, r.claim_uom, r.claim_unit_amount, r.claim_amount, r.flags
    FROM calculation_results r
    JOIN calculation_runs cr ON cr.tenant_id = r.tenant_id AND cr.id = r.run_id AND cr.status = 'completed'
    JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id AND s.is_current
    WHERE r.tenant_id = :t
    ORDER BY r.sales_document_line_id, cr.completed_at DESC, r.id DESC
)
"""
UNCLAIMED_REFERENCE = (
    "NOT EXISTS (WITH RECURSIVE anc(id) AS (SELECT l.sales_document_line_id "
    "UNION SELECT p.id FROM sales_document_lines p JOIN anc ON p.superseded_by_id = anc.id "
    "AND p.tenant_id = l.tenant_id) "
    "SELECT 1 FROM claim_lines c WHERE c.tenant_id = l.tenant_id "
    "AND c.sales_document_line_id IN (SELECT id FROM anc) AND c.released_at IS NULL)"
)


def _rows(admin: Engine, latest: str, unclaimed: str, tenant_id: str, status: str) -> list[tuple[Any, ...]]:
    sql = (
        f"WITH {latest} SELECT {missed.COLUMNS}, l.status, l.exception_code FROM latest l "
        f"WHERE l.status = :s AND {unclaimed} ORDER BY l.id DESC"
    )
    with admin.connect() as c:
        return [tuple(r) for r in c.execute(text(sql), {"t": tenant_id, "s": status})]


def assert_same_as_reference(admin: Engine, tenant_id: str) -> int:
    """Old and new SQL return the same rows (every column) in the same order, for eligible and exception lines.
    Both LATEST variants are checked. Returns the number of eligible rows compared."""
    for status in ("eligible", "exception"):
        old = _rows(admin, LATEST_REFERENCE, UNCLAIMED_REFERENCE, tenant_id, status)
        for variant in (missed.LATEST, missed.LATEST_SCAN):
            new = _rows(admin, variant, missed.UNCLAIMED, tenant_id, status)
            assert new == old, f"{status}: {len(new)} rows vs reference {len(old)}"
        if status == "eligible":
            n = len(new)
    return n
