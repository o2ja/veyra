"""Read-only claim data for remittance reconciliation (S8, BUSINESS_LOGIC.md §10.1): the manufacturer's submitted,
unreleased claim lines with the invoice facts the matching tiers compare, and the approved adjustments included in
submitted batches. Set-based and tenant-scoped; the ``reconciliation`` module calls these, never the tables."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def submitted_claim_lines(db: Session, tenant_id: UUID, manufacturer_id: UUID) -> list[Any]:
    """Columns: id, batch_id, claim_reference, invoice_no, invoice_line, invoice_date, sku, quantity, claim_amount,
    submitted_at (the batch's submission date). ``invoice_*`` and ``sku`` are NULL for rebate accrual lines."""
    return list(
        db.execute(
            text(
                "SELECT c.id, c.batch_id, c.claim_reference, s.document_number AS invoice_no, s.line_number AS invoice_line, "
                "s.invoice_date, COALESCE(p.sku, s.sku_raw) AS sku, c.quantity, c.claim_amount, "
                "b.submitted_at "
                "FROM claim_lines c JOIN claim_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id "
                "LEFT JOIN sales_document_lines s ON s.tenant_id = c.tenant_id AND s.id = c.sales_document_line_id "
                "LEFT JOIN products p ON p.tenant_id = s.tenant_id AND p.id = s.product_id "
                "WHERE c.tenant_id = :t AND b.manufacturer_id = :m AND b.status = 'submitted' "
                "AND c.released_at IS NULL ORDER BY c.id"
            ),
            {"t": tenant_id, "m": manufacturer_id},
        )
    )


def included_adjustments(db: Session, tenant_id: UUID, manufacturer_id: UUID) -> list[Any]:
    """Columns: id, claim_line_id, batch_id (the batch that carries it), amount, for adjustments included in a
    submitted batch of the manufacturer."""
    return list(
        db.execute(
            text(
                "SELECT a.id, a.claim_line_id, a.included_in_batch_id AS batch_id, a.amount "
                "FROM claim_adjustments a JOIN claim_batches b "
                "ON b.tenant_id = a.tenant_id AND b.id = a.included_in_batch_id "
                "WHERE a.tenant_id = :t AND a.manufacturer_id = :m AND a.status = 'included' "
                "AND a.claim_line_id IS NOT NULL AND b.status = 'submitted' ORDER BY a.id"
            ),
            {"t": tenant_id, "m": manufacturer_id},
        )
    )


def claim_references(db: Session, tenant_id: UUID, claim_line_ids: list[UUID]) -> dict[UUID, str]:
    """claim line id -> claim reference, for displaying matches."""
    rows = db.execute(
        text(
            "SELECT id, claim_reference FROM claim_lines WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"t": tenant_id, "ids": claim_line_ids},
    )
    return {r.id: r.claim_reference for r in rows}


def claim_line_refs(db: Session, tenant_id: UUID, claim_line_ids: list[UUID]) -> dict[UUID, Any]:
    """claim line id -> (claim_reference, batch_id, sales_document_line_id, claim_amount), for links in other views."""
    if not claim_line_ids:
        return {}
    rows = db.execute(
        text(
            "SELECT id, claim_reference, batch_id, sales_document_line_id, claim_amount FROM claim_lines "
            "WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"t": tenant_id, "ids": claim_line_ids},
    )
    return {r.id: r for r in rows}
