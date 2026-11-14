# ruff: noqa: S608  # the SQL is assembled from module constants only; every value is a bound parameter
"""Read-only claim data for the dashboard, trace resolver and audit pack (S10; FR-RP-01/03, W14). Set-based and
tenant-scoped; the ``reporting`` module calls these through ``rebate.modules.claims``, never the tables.

Figure ``claimed`` (owner decision 2026-11-13, D-N6): an adjustment belongs to the batch that carries it, so the figure
is the claim lines of submitted batches that are still active (``released_at IS NULL``) plus the adjustments included
in a submitted batch (``recovery_reclaim`` too), by the month that batch was submitted (a DATE, truncated explicitly
so the session time zone cannot move it). Branch = the invoice branch of the original claim line, if any."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.core.figures import FigureSet
from rebate.core.trace import node

_BRANCH = (
    "LEFT JOIN sales_document_lines s ON s.tenant_id = c.tenant_id AND s.id = c.sales_document_line_id "
    "LEFT JOIN sales_documents d ON d.tenant_id = s.tenant_id AND d.id = s.document_id "
)

figures = FigureSet(
    {
        "claimed": (
            "SELECT c.id, 'claim_line' AS trace_kind, c.id AS trace_id, b.manufacturer_id, d.branch_id, "  # nosec B608 (module constants and fixed column names; every value is a bound parameter)
            "date_trunc('month', b.submitted_at::timestamp)::date AS month, c.claim_amount AS amount, "
            "c.claim_reference AS label "
            f"FROM claim_lines c JOIN claim_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id {_BRANCH}"
            "WHERE c.tenant_id = :t AND b.status = 'submitted' AND c.released_at IS NULL "
            "UNION ALL "
            "SELECT a.id, 'claim_adjustment' AS trace_kind, a.id AS trace_id, b.manufacturer_id, d.branch_id, "
            "date_trunc('month', b.submitted_at::timestamp)::date AS month, a.amount AS amount, "
            "a.adjustment_type || ' adjustment' AS label "
            "FROM claim_adjustments a JOIN claim_batches b ON b.tenant_id = a.tenant_id "
            "AND b.id = a.included_in_batch_id "
            "LEFT JOIN claim_lines c ON c.tenant_id = a.tenant_id AND c.id = a.claim_line_id "
            f"{_BRANCH}"
            "WHERE a.tenant_id = :t AND a.status = 'included' AND b.status = 'submitted'"
        )
    }
)

_SOURCE = (
    "LEFT JOIN import_batches ib ON ib.tenant_id = s.tenant_id AND ib.id = s.import_batch_id "
    "LEFT JOIN file_objects f ON f.tenant_id = ib.tenant_id AND f.id = ib.file_id "
)


def _sales_nodes(r: Any) -> list[dict[str, Any]]:
    """Invoice line and the file row it was imported from."""
    if r.sales_document_line_id is None:
        return []
    return [
        node(
            "sales_line",
            r.sales_document_line_id,
            f"{r.document_number}/{r.line_number}",
            invoice_date=r.invoice_date,
            sku=r.sku_raw,
            extended_amount=r.extended_amount,
        ),
        node(
            "source_row",
            r.file_id,
            f"{r.file_name} row {r.source_row_number}",
            file_name=r.file_name,
            row_number=r.source_row_number,
            import_batch_id=r.import_batch_id,
        ),
    ]


def trace_claim_adjustment(
    db: Session, tenant: UUID, adjustment_id: UUID
) -> tuple[list[dict[str, Any]], UUID | None] | None:
    """The adjustment, its carrying batch and the claim line it corrects (to follow on). ``None`` if unknown."""
    r = db.execute(
        text(
            "SELECT a.id, a.adjustment_type, a.amount, a.status, a.reason, a.claim_line_id, a.variance_case_id, "
            "b.id AS batch_id, b.batch_number, b.status AS batch_status, b.submitted_at "
            "FROM claim_adjustments a LEFT JOIN claim_batches b ON b.tenant_id = a.tenant_id "
            "AND b.id = a.included_in_batch_id WHERE a.tenant_id = :t AND a.id = :id"
        ),
        {"t": tenant, "id": adjustment_id},
    ).first()
    if r is None:
        return None
    nodes = [
        node(
            "claim_adjustment",
            r.id,
            f"{r.adjustment_type} adjustment",
            amount=r.amount,
            status=r.status,
            reason=r.reason,
            variance_case_id=r.variance_case_id,
        )
    ]
    if r.batch_id is not None:
        nodes.append(
            node(
                "claim_batch", r.batch_id, r.batch_number, status=r.batch_status, submitted_at=r.submitted_at
            )
        )
    return nodes, r.claim_line_id


def trace_claim_line(db: Session, tenant: UUID, claim_line_id: UUID) -> list[dict[str, Any]] | None:
    """Claim line, its batch, agreement version, calculation result (or rebate accrual) and invoice line back to the
    imported file row. ``None`` when the claim line does not exist."""
    r = db.execute(
        text(
            "SELECT c.id, c.claim_reference, c.source_kind, c.claim_amount, c.quantity, c.unit_amount, "  # nosec B608 (module constants and fixed column names; every value is a bound parameter)
            "c.calculation_result_id, c.agreement_version_id, c.rebate_accrual_entry_id, c.sales_document_line_id, "
            "c.released_at, b.id AS batch_id, b.batch_number, b.status AS batch_status, b.submitted_at, "
            "av.version_number, ag.external_number, ag.name AS agreement_name, "
            "s.document_number, s.line_number, s.invoice_date, s.sku_raw, s.extended_amount, s.source_row_number, "
            "s.import_batch_id, ib.file_id, f.original_name AS file_name, "
            "e.period_start, e.period_end, e.cumulative_earned, e.delta "
            "FROM claim_lines c JOIN claim_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id "
            "JOIN agreement_versions av ON av.tenant_id = c.tenant_id AND av.id = c.agreement_version_id "
            "JOIN agreements ag ON ag.tenant_id = av.tenant_id AND ag.id = av.agreement_id "
            "LEFT JOIN sales_document_lines s ON s.tenant_id = c.tenant_id AND s.id = c.sales_document_line_id "
            f"{_SOURCE}"
            "LEFT JOIN rebate_accrual_entries e ON e.tenant_id = c.tenant_id AND e.id = c.rebate_accrual_entry_id "
            "WHERE c.tenant_id = :t AND c.id = :id"
        ),
        {"t": tenant, "id": claim_line_id},
    ).first()
    if r is None:
        return None
    chain = [
        node(
            "claim_line",
            r.id,
            r.claim_reference,
            source_kind=r.source_kind,
            claim_amount=r.claim_amount,
            quantity=r.quantity,
            unit_amount=r.unit_amount,
            released_at=r.released_at,
        ),
        node("claim_batch", r.batch_id, r.batch_number, status=r.batch_status, submitted_at=r.submitted_at),
        node(
            "agreement_version",
            r.agreement_version_id,
            f"{r.external_number} v{r.version_number}",
            agreement_name=r.agreement_name,
        ),
    ]
    if r.calculation_result_id is not None:
        chain.append(node("calculation_result", r.calculation_result_id, "Calculation result"))
    if (
        r.rebate_accrual_entry_id is not None
    ):  # a rebate claim aggregates many purchases: it has no single file row
        chain.append(
            node(
                "accrual_entry",
                r.rebate_accrual_entry_id,
                f"Rebate accrual {r.period_start} to {r.period_end}",
                cumulative_earned=r.cumulative_earned,
                delta=r.delta,
            )
        )
    return chain + _sales_nodes(r)


def trace_calculation_result(db: Session, tenant: UUID, result_id: UUID) -> list[dict[str, Any]] | None:
    r = db.execute(
        text(
            "SELECT r.id, r.status, r.exception_code, r.claim_amount, r.run_id, r.agreement_version_id, "  # nosec B608 (module constants and fixed column names; every value is a bound parameter)
            "r.sales_document_line_id, av.version_number, ag.external_number, "
            "s.document_number, s.line_number, s.invoice_date, s.sku_raw, s.extended_amount, s.source_row_number, "
            "s.import_batch_id, ib.file_id, f.original_name AS file_name "
            "FROM calculation_results r "
            "LEFT JOIN agreement_versions av ON av.tenant_id = r.tenant_id AND av.id = r.agreement_version_id "
            "LEFT JOIN agreements ag ON ag.tenant_id = av.tenant_id AND ag.id = av.agreement_id "
            "JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id "
            f"{_SOURCE}WHERE r.tenant_id = :t AND r.id = :id"
        ),
        {"t": tenant, "id": result_id},
    ).first()
    if r is None:
        return None
    chain = [
        node(
            "calculation_result",
            r.id,
            "Calculation result",
            status=r.status,
            exception_code=r.exception_code,
            claim_amount=r.claim_amount,
            run_id=r.run_id,
        )
    ]
    if r.agreement_version_id is not None:
        chain.append(
            node("agreement_version", r.agreement_version_id, f"{r.external_number} v{r.version_number}")
        )
    return chain + _sales_nodes(r)


def audit_pack_rows(db: Session, tenant: UUID, batch_id: UUID) -> list[dict[str, Any]] | None:
    """One row per claim line of the batch (invoice line -> agreement version -> claim) and per adjustment included in
    it (signed), so the pack ties out to the batch total. ``None`` when the batch does not exist."""
    p = {"t": tenant, "b": batch_id}
    batch = db.execute(
        text("SELECT batch_number, status FROM claim_batches WHERE tenant_id = :t AND id = :b"), p
    ).first()
    if batch is None:
        return None
    lines = db.execute(
        text(
            "SELECT c.id, c.claim_reference, c.source_kind AS line_type, c.quantity, c.unit_amount, c.claim_amount, "  # nosec B608 (module constants and fixed column names; every value is a bound parameter)
            "ag.external_number AS agreement_number, av.version_number, "
            "s.document_number AS invoice_number, s.line_number AS invoice_line, s.invoice_date, "
            "s.sku_raw AS sku, f.original_name AS source_file, s.source_row_number AS source_row "
            "FROM claim_lines c "
            "JOIN agreement_versions av ON av.tenant_id = c.tenant_id AND av.id = c.agreement_version_id "
            "JOIN agreements ag ON ag.tenant_id = av.tenant_id AND ag.id = av.agreement_id "
            "LEFT JOIN sales_document_lines s ON s.tenant_id = c.tenant_id AND s.id = c.sales_document_line_id "
            f"{_SOURCE}WHERE c.tenant_id = :t AND c.batch_id = :b ORDER BY c.claim_reference"
        ),
        p,
    )
    adjustments = db.execute(
        text(
            "SELECT a.id, 'adjustment:' || a.adjustment_type AS line_type, c.claim_reference, "
            "a.amount AS claim_amount, a.reason "
            "FROM claim_adjustments a LEFT JOIN claim_lines c ON c.tenant_id = a.tenant_id AND c.id = a.claim_line_id "
            "WHERE a.tenant_id = :t AND a.included_in_batch_id = :b ORDER BY a.id"
        ),
        p,
    )
    head = {"batch_number": batch.batch_number, "batch_status": batch.status}
    return [{**head, "kind": "claim_line", **r._mapping} for r in lines] + [
        {**head, "kind": "adjustment", **r._mapping} for r in adjustments
    ]
