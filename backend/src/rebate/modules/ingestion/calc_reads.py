# ruff: noqa: S608  # the SQL is assembled from module constants only; every value is a bound parameter
"""Narrow, read-only source-line queries for the calculation runs (``claims`` module).

Lines are those of import batches that finished (``completed`` / ``completed_with_errors``) at or before the run's
data cutoff; only current versions of a line count. Set-based, tenant-scoped."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

_BATCH = (
    "JOIN import_batches b ON b.tenant_id = l.tenant_id AND b.id = l.import_batch_id "
    "AND b.status IN ('completed', 'completed_with_errors') AND b.completed_at <= :cutoff "
)
_MFR = "AND l.manufacturer_id IS NOT DISTINCT FROM CAST(:m AS uuid) "


def sales_rows(
    db: Session,
    tenant_id: UUID,
    manufacturer_id: UUID | None,
    date_from: date,
    date_to: date,
    cutoff: datetime,
    *,
    product_ids: list[UUID] | None = None,
    limit: int | None = None,
) -> list[Any]:
    """Columns: id, document_number, line_number, invoice_date, ship_date, customer_id, product_id, manufacturer_id,
    base_uom, quantity, uom, unit_price, unit_cost, replacement_cost, spa_reference, original_line_id, row_hash.
    ``manufacturer_id`` None = lines without a known manufacturer."""
    sql = (
        "SELECT l.id, l.document_number, l.line_number, l.invoice_date, l.ship_date, l.customer_id, l.product_id, "
        "l.manufacturer_id, p.base_uom, l.quantity, l.uom, l.unit_price, l.unit_cost, l.replacement_cost, "
        "l.spa_reference, l.original_line_id, l.row_hash FROM sales_document_lines l "
        + _BATCH
        + "LEFT JOIN products p ON p.tenant_id = l.tenant_id AND p.id = l.product_id "
        "WHERE l.tenant_id = :t AND l.is_current " + _MFR + "AND l.invoice_date BETWEEN :d1 AND :d2 "
    )
    params: dict[str, Any] = {
        "t": tenant_id,
        "m": manufacturer_id,
        "cutoff": cutoff,
        "d1": date_from,
        "d2": date_to,
    }
    if product_ids is not None:
        sql += "AND l.product_id = ANY(CAST(:products AS uuid[])) "
        params["products"] = product_ids
    if limit is not None:
        sql += "ORDER BY l.invoice_date DESC, l.id DESC LIMIT :limit"
        params["limit"] = limit
    else:
        sql += "ORDER BY l.id"
    return list(db.execute(text(sql), params))


def returned_quantities(
    db: Session, tenant_id: UUID, manufacturer_id: UUID | None, cutoff: datetime
) -> dict[UUID, Decimal]:
    """Original line id -> sum of |quantity| of its linked current return lines dated on or before the cutoff."""
    rows = db.execute(
        text(
            "SELECT l.original_line_id, sum(-l.quantity) AS returned FROM sales_document_lines l "
            + _BATCH
            + "WHERE l.tenant_id = :t AND l.is_current AND l.quantity < 0 AND l.original_line_id IS NOT NULL "
            "AND l.invoice_date <= :cutoff_date " + _MFR + "GROUP BY l.original_line_id"
        ),
        {"t": tenant_id, "m": manufacturer_id, "cutoff": cutoff, "cutoff_date": cutoff.date()},
    )
    return {r.original_line_id: r.returned for r in rows}


def line_quantities(db: Session, tenant_id: UUID, line_ids: list[UUID]) -> dict[UUID, Decimal]:
    return {
        r.id: r.quantity
        for r in db.execute(
            text(
                "SELECT id, quantity FROM sales_document_lines WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"t": tenant_id, "ids": line_ids},
        )
    }


def sales_line_windows(
    db: Session,
    tenant_id: UUID,
    manufacturer_id: UUID | None,
    date_from: date,
    date_to: date,
    cutoff: datetime,
    max_lines: int,
) -> list[tuple[date, date]]:
    """Consecutive invoice-date windows of at most ``max_lines`` lines; one date is never split."""
    rows = db.execute(
        text(
            "SELECT l.invoice_date AS d, count(*) AS n FROM sales_document_lines l "
            + _BATCH
            + "WHERE l.tenant_id = :t AND l.is_current "
            + _MFR
            + "AND l.invoice_date BETWEEN :d1 AND :d2 GROUP BY l.invoice_date ORDER BY l.invoice_date"
        ),
        {"t": tenant_id, "m": manufacturer_id, "cutoff": cutoff, "d1": date_from, "d2": date_to},
    ).all()
    out: list[tuple[date, date]] = []
    start: date | None = None
    last: date | None = None
    count = 0
    for r in rows:
        if start is not None and last is not None and count + r.n > max_lines:
            out.append((start, last))
            start, count = None, 0
        if start is None:
            start = r.d
        last, count = r.d, count + r.n
    if start is not None and last is not None:
        out.append((start, last))
    return out


def sales_line_ids(
    db: Session,
    tenant_id: UUID,
    manufacturer_id: UUID | None,
    date_from: date,
    date_to: date,
    cutoff: datetime,
) -> list[UUID]:
    return list(
        db.scalars(
            text(
                "SELECT l.id FROM sales_document_lines l "
                + _BATCH
                + "WHERE l.tenant_id = :t AND l.is_current "
                + _MFR
                + "AND l.invoice_date BETWEEN :d1 AND :d2"
            ),
            {"t": tenant_id, "m": manufacturer_id, "cutoff": cutoff, "d1": date_from, "d2": date_to},
        )
    )


def sales_volume_rows(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, date_from: date, date_to: date, cutoff: datetime
) -> list[Any]:
    """Columns: id, invoice_date, product_id, quantity, extended_amount (signed), spa_reference, row_hash."""
    return list(
        db.execute(
            text(
                "SELECT l.id, l.invoice_date, l.product_id, l.quantity, l.extended_amount, l.spa_reference, l.row_hash "
                "FROM sales_document_lines l "
                + _BATCH
                + "WHERE l.tenant_id = :t AND l.is_current "
                + _MFR
                + "AND l.invoice_date BETWEEN :d1 AND :d2 AND l.product_id IS NOT NULL ORDER BY l.id"
            ),
            {"t": tenant_id, "m": manufacturer_id, "cutoff": cutoff, "d1": date_from, "d2": date_to},
        )
    )


def purchase_volume_rows(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, date_from: date, date_to: date, cutoff: datetime
) -> list[Any]:
    """Columns: id, document_date, product_id, quantity, extended_amount (signed), row_hash."""
    return list(
        db.execute(
            text(
                "SELECT l.id, l.document_date, l.product_id, l.quantity, l.extended_amount, l.row_hash "
                "FROM purchase_document_lines l "
                + _BATCH
                + "WHERE l.tenant_id = :t AND l.is_current AND l.manufacturer_id = :m "
                "AND l.document_date BETWEEN :d1 AND :d2 AND l.product_id IS NOT NULL ORDER BY l.id"
            ),
            {"t": tenant_id, "m": manufacturer_id, "cutoff": cutoff, "d1": date_from, "d2": date_to},
        )
    )
