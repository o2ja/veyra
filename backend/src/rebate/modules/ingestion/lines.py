"""Browse normalised source lines with lineage (S3.4; API_SPECIFICATION.md §2.4, DATABASE_SCHEMA.md §6):
every line carries its import batch, the file behind it and its row number in that file."""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, aliased

from rebate.core.errors import NotFound
from rebate.modules import masterdata
from rebate.modules.ingestion.models import ImportBatch, PurchaseDocumentLine, SalesDocumentLine


@dataclass(frozen=True)
class LineFilter:
    manufacturer_id: UUID | None = None
    customer_id: UUID | None = None
    product_id: UUID | None = None
    import_batch_id: UUID | None = None
    document_number: str | None = None
    doc_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    current: bool | None = True


def _with_file(model: Any) -> Select[Any]:
    return select(model, ImportBatch.file_id).join(
        ImportBatch, (ImportBatch.tenant_id == model.tenant_id) & (ImportBatch.id == model.import_batch_id)
    )


def sales_lines(db: Session, tenant_id: UUID, f: LineFilter, limit: int, cursor: str | None) -> list[Any]:
    m = SalesDocumentLine
    stmt = _with_file(m).where(m.tenant_id == tenant_id)
    if f.customer_id is not None:
        stmt = stmt.where(m.customer_id == f.customer_id)
    if f.date_from is not None:
        stmt = stmt.where(m.invoice_date >= f.date_from)
    if f.date_to is not None:
        stmt = stmt.where(m.invoice_date <= f.date_to)
    return list(db.execute(masterdata.keyset(_common(stmt, m, f), m.id, cursor, limit)).all())


def purchase_lines(db: Session, tenant_id: UUID, f: LineFilter, limit: int, cursor: str | None) -> list[Any]:
    m = PurchaseDocumentLine
    stmt = _with_file(m).where(m.tenant_id == tenant_id)
    if f.date_from is not None:
        stmt = stmt.where(m.document_date >= f.date_from)
    if f.date_to is not None:
        stmt = stmt.where(m.document_date <= f.date_to)
    return list(db.execute(masterdata.keyset(_common(stmt, m, f), m.id, cursor, limit)).all())


def _common(stmt: Select[Any], m: Any, f: LineFilter) -> Select[Any]:
    if f.manufacturer_id is not None:
        stmt = stmt.where(m.manufacturer_id == f.manufacturer_id)
    if f.product_id is not None:
        stmt = stmt.where(m.product_id == f.product_id)
    if f.import_batch_id is not None:
        stmt = stmt.where(m.import_batch_id == f.import_batch_id)
    if f.document_number is not None:
        stmt = stmt.where(m.document_number == f.document_number)
    if f.doc_type is not None:
        stmt = stmt.where(m.doc_type == f.doc_type)
    if f.current is not None:
        stmt = stmt.where(m.is_current == f.current)
    return stmt


def sales_line(db: Session, tenant_id: UUID, line_id: UUID) -> tuple[SalesDocumentLine, UUID, UUID | None]:
    """The line, its source file, and the version it replaced (if any)."""
    row = db.execute(
        _with_file(SalesDocumentLine).where(
            SalesDocumentLine.tenant_id == tenant_id, SalesDocumentLine.id == line_id
        )
    ).first()
    if row is None:
        raise NotFound()
    line, file_id = row
    previous = db.scalar(
        select(SalesDocumentLine.id).where(
            SalesDocumentLine.tenant_id == tenant_id, SalesDocumentLine.superseded_by_id == line.id
        )
    )
    return line, file_id, previous


CHANGE_FIELDS = (
    "quantity",
    "unit_price",
    "unit_cost",
    "replacement_cost",
    "extended_amount",
    "uom",
    "invoice_date",
    "ship_date",
    "customer_id",
    "product_id",
    "sku_raw",
    "spa_reference",
    "project_reference",
    "description",
    "doc_type",
    "currency_code",
)
PURCHASE_CHANGE_FIELDS = (
    "quantity",
    "unit_cost",
    "extended_amount",
    "uom",
    "document_date",
    "product_id",
    "sku_raw",
    "doc_type",
)


def changes(
    db: Session, tenant_id: UUID, batch: ImportBatch, limit: int, cursor: str | None
) -> list[tuple[Any, Any, dict[str, list[Any]]]]:
    """Lines of this batch that replaced an earlier version: ``(new, old, {field: [old, new]})``."""
    model: Any = SalesDocumentLine if batch.import_type == "sales" else PurchaseDocumentLine
    fields = CHANGE_FIELDS if batch.import_type == "sales" else PURCHASE_CHANGE_FIELDS
    old = aliased(model)
    stmt = (
        select(model, old)
        .join(old, (old.tenant_id == model.tenant_id) & (old.superseded_by_id == model.id))
        .where(model.tenant_id == tenant_id, model.import_batch_id == batch.id)
    )
    out = []
    for new, prev in db.execute(masterdata.keyset(stmt, model.id, cursor, limit, newest_first=False)).all():
        diff = {
            k: [_plain(getattr(prev, k)), _plain(getattr(new, k))]
            for k in fields
            if getattr(prev, k) != getattr(new, k)
        }
        out.append((new, prev, diff))
    return out


def _plain(value: Any) -> Any:
    return None if value is None else str(value)
