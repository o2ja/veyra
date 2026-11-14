"""Read-only reconciliation data for the dashboard, trace resolver and audit pack (S10; FR-RP-01/03, W14). The
``reporting`` module calls these through ``rebate.modules.reconciliation``, never the tables.

Figure ``collected`` (owner decision 2026-11-13, D-N6): cash on matched lines = confirmed allocations of accepted or
reconciled remittances to claim lines and to adjustments (return reversals, recovery reclaims); an unallocated
remainder is not collected. Month = the payment date (else the document date, else the day the document was created in
the business time zone), truncated explicitly (the dates are business dates; the session zone must not move them). The
branch is the invoice's branch of the claim line, or of the adjustment's original line."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.core.figures import FigureSet
from rebate.core.trace import node

figures = FigureSet(
    {
        "collected": (
            "SELECT m.id, 'remittance_line' AS trace_kind, m.remittance_line_id AS trace_id, d.manufacturer_id, "
            "sd.branch_id, date_trunc('month', COALESCE(d.payment_date, d.document_date, "
            "(d.created_at AT TIME ZONE :tz)::date)::timestamp)::date AS month, m.allocated_amount AS amount, "
            "COALESCE(d.document_number, 'remittance') || ' line ' || l.line_number AS label "
            "FROM reconciliation_matches m "
            "JOIN remittance_lines l ON l.tenant_id = m.tenant_id AND l.id = m.remittance_line_id "
            "JOIN remittance_documents d ON d.tenant_id = l.tenant_id AND d.id = l.remittance_document_id "
            "LEFT JOIN claim_adjustments a ON a.tenant_id = m.tenant_id AND a.id = m.claim_adjustment_id "
            "LEFT JOIN claim_lines c ON c.tenant_id = m.tenant_id AND c.id = COALESCE(m.claim_line_id, a.claim_line_id) "
            "LEFT JOIN sales_document_lines s ON s.tenant_id = c.tenant_id AND s.id = c.sales_document_line_id "
            "LEFT JOIN sales_documents sd ON sd.tenant_id = s.tenant_id AND sd.id = s.document_id "
            "WHERE m.tenant_id = :t AND m.status = 'confirmed' "
            "AND d.status IN ('accepted', 'reconciled')"
        )
    }
)

_LINE = (
    "SELECT l.id, l.line_number, l.source_row_number, l.invoice_number, l.invoice_line_number, l.amount_paid, "
    "l.external_reason_code, d.id AS document_id, d.document_number, d.status AS document_status, d.file_id, "
    "d.payment_date, f.original_name AS file_name "
    "FROM remittance_lines l JOIN remittance_documents d ON d.tenant_id = l.tenant_id "
    "AND d.id = l.remittance_document_id "
    "LEFT JOIN file_objects f ON f.tenant_id = d.tenant_id AND f.id = d.file_id "
)


def _line_nodes(r: Any) -> list[dict[str, Any]]:
    """Remittance line, its document and the file row it was read from."""
    return [
        node(
            "remittance_line",
            r.id,
            f"{r.document_number or 'remittance'} line {r.line_number}",
            amount_paid=r.amount_paid,
            invoice_number=r.invoice_number,
            invoice_line_number=r.invoice_line_number,
            external_reason_code=r.external_reason_code,
        ),
        node(
            "remittance_document",
            r.document_id,
            r.document_number or "remittance",
            status=r.document_status,
            payment_date=r.payment_date,
        ),
        node(
            "source_row",
            r.file_id,
            f"{r.file_name} row {r.source_row_number}" if r.source_row_number else str(r.file_name),
            file_name=r.file_name,
            row_number=r.source_row_number,
        ),
    ]


def _match_node(m: Any) -> dict[str, Any]:
    return node(
        "match",
        m.id,
        f"{m.status} match ({m.method})",
        allocated_amount=m.allocated_amount,
        claim_line_id=m.claim_line_id,
        claim_adjustment_id=m.claim_adjustment_id,
    )


def trace_remittance_line(
    db: Session, tenant: UUID, remittance_line_id: UUID
) -> tuple[list[dict[str, Any]], list[UUID]] | None:
    """The line, its document, its file row and its matches; plus the claim lines it is matched to (to follow on).
    ``None`` when the line does not exist."""
    r = db.execute(
        text(f"{_LINE}WHERE l.tenant_id = :t AND l.id = :id"), {"t": tenant, "id": remittance_line_id}
    ).first()
    if r is None:
        return None
    matches = db.execute(
        text(
            "SELECT id, status, method, allocated_amount, claim_line_id, claim_adjustment_id "
            "FROM reconciliation_matches WHERE tenant_id = :t AND remittance_line_id = :id ORDER BY id"
        ),
        {"t": tenant, "id": remittance_line_id},
    ).all()
    claim_lines = list(dict.fromkeys(m.claim_line_id for m in matches if m.claim_line_id is not None))
    return _line_nodes(r) + [_match_node(m) for m in matches], claim_lines


def trace_payments(db: Session, tenant: UUID, claim_line_id: UUID) -> list[dict[str, Any]]:
    """What was paid against a claim line: each match with its remittance line, document and file row."""
    out: list[dict[str, Any]] = []
    for m in db.execute(
        text(
            "SELECT id, status, method, allocated_amount, claim_line_id, claim_adjustment_id, remittance_line_id "
            "FROM reconciliation_matches WHERE tenant_id = :t AND claim_line_id = :id ORDER BY id"
        ),
        {"t": tenant, "id": claim_line_id},
    ).all():
        r = db.execute(
            text(f"{_LINE}WHERE l.tenant_id = :t AND l.id = :id"), {"t": tenant, "id": m.remittance_line_id}
        ).one()
        out += [_match_node(m), *_line_nodes(r)]
    return out


def confirmed_payments(
    db: Session, tenant: UUID, claim_line_ids: list[UUID], adjustment_ids: list[UUID]
) -> dict[UUID, tuple[Any, str]]:
    """claim line or adjustment id -> (sum of confirmed allocations, remittance document numbers)."""
    rows = db.execute(
        text(
            "SELECT COALESCE(m.claim_line_id, m.claim_adjustment_id) AS target, sum(m.allocated_amount) AS paid, "
            "string_agg(DISTINCT COALESCE(d.document_number, 'remittance'), '; ') AS documents "
            "FROM reconciliation_matches m "
            "JOIN remittance_lines l ON l.tenant_id = m.tenant_id AND l.id = m.remittance_line_id "
            "JOIN remittance_documents d ON d.tenant_id = l.tenant_id AND d.id = l.remittance_document_id "
            "WHERE m.tenant_id = :t AND m.status = 'confirmed' AND d.status IN ('accepted', 'reconciled') "
            "AND (m.claim_line_id = ANY(CAST(:cl AS uuid[])) OR m.claim_adjustment_id = ANY(CAST(:adj AS uuid[]))) "
            "GROUP BY 1"
        ),
        {"t": tenant, "cl": claim_line_ids, "adj": adjustment_ids},
    )
    return {r.target: (r.paid, r.documents) for r in rows}
