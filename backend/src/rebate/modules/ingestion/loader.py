"""Loader: validated staged rows -> source tables, one chunk per call (DATABASE_SCHEMA.md §5).

Per natural key ``(source_system, document_number, line_number)`` among *current* lines:

* no current line -> insert version 1 (**inserted**);
* same ``row_hash`` -> nothing (**unchanged**);
* different hash -> the current line is retired (``is_current = false``, ``superseded_by_id``) and the
  new version inserted (**changed**); if the retired line is in an active claim (``claims_hook``) the
  new version is flagged ``source_changed``.

Document headers are versioned the same way by a hash of their header fields. Retire-then-insert uses
``INSERT ... ON CONFLICT DO NOTHING`` on the partial unique natural-key index: a row that conflicts
means another writer got there first, so the chunk aborts (rolls back) and the job retries instead of
writing a duplicate. The caller holds the batch row lock, so chunks of one batch never interleave.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Table, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebate.core.ids import new_id
from rebate.modules.ingestion import claims_hook
from rebate.modules.ingestion.models import (
    ImportBatch,
    PurchaseDocument,
    PurchaseDocumentLine,
    SalesDocument,
    SalesDocumentLine,
)
from rebate.modules.ingestion.validators import row_hash


class ConcurrentLoad(Exception):
    """A natural key was inserted by someone else meanwhile: retry the chunk (transient)."""


@dataclass
class Counts:
    inserted: int = 0
    unchanged: int = 0
    changed: int = 0
    source_changed: int = 0
    changed_line_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class Staged:
    row_number: int
    normalized: dict[str, Any]
    row_hash: str


def _d(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)


def _insert_all(db: Session, table: Table, rows: list[dict[str, Any]], key: list[str]) -> None:
    if not rows:
        return
    stmt = (
        insert(table)
        .on_conflict_do_nothing(index_elements=key, index_where=table.c.is_current)
        .returning(table.c.id)
    )
    if len(db.execute(stmt, rows).all()) != len(rows):  # executemany, batched ("insertmanyvalues")
        raise ConcurrentLoad()


def _retire(db: Session, table: Table, pairs: list[tuple[UUID, UUID]]) -> None:
    """``(old id, new id)``: old leaves the current set; the deferred FK lets new arrive after."""
    for old, new in pairs:
        db.execute(
            update(table)
            .where(table.c.id == old, table.c.is_current)
            .values(is_current=False, superseded_by_id=new)
        )


def _documents(
    db: Session,
    batch: ImportBatch,
    model: Any,
    headers: dict[tuple[str, str], dict[str, Any]],
) -> dict[tuple[str, str], UUID]:
    """Current header per ``(doc_type, number)``: reused when its hash matches, else a new version."""
    table: Table = model.__table__
    existing = {
        (d.doc_type, d.document_number): d
        for d in db.scalars(
            select(model)
            .where(
                model.tenant_id == batch.tenant_id,
                model.source_system == batch.source_system,
                model.is_current,
                tuple_(model.doc_type, model.document_number).in_(list(headers)),
            )
            .with_for_update()
        )
    }
    ids: dict[tuple[str, str], UUID] = {}
    new_rows: list[dict[str, Any]] = []
    retire: list[tuple[UUID, UUID]] = []
    for key, values in headers.items():
        digest = row_hash({k: v for k, v in values.items() if not k.endswith("_id")})
        current = existing.get(key)
        if current is not None and current.row_hash == digest:
            ids[key] = current.id
            continue
        doc_id = new_id()
        ids[key] = doc_id
        new_rows.append(
            {
                **_typed_header(model, values),
                "tenant_id": batch.tenant_id,
                "id": doc_id,
                "import_batch_id": batch.id,
                "source_system": batch.source_system,
                "doc_type": key[0],
                "document_number": key[1],
                "row_hash": digest,
                "version": 1 if current is None else current.version + 1,
            }
        )
        if current is not None:
            retire.append((current.id, doc_id))
    _retire(db, table, retire)
    _insert_all(db, table, new_rows, ["tenant_id", "source_system", "doc_type", "document_number"])
    return ids


def _typed_header(model: Any, v: dict[str, Any]) -> dict[str, Any]:
    if model is SalesDocument:
        return {
            "document_date": _date(v["document_date"]),
            "customer_id": _uuid(v["customer_id"]),
            "branch_id": _uuid(v["branch_id"]),
            "original_document_id": _uuid(v.get("original_document_id")),
            "currency_code": v["currency_code"],
        }
    return {
        "document_date": _date(v["document_date"]),
        "manufacturer_id": _uuid(v["manufacturer_id"]),
        "po_number": v["po_number"],
        "currency_code": v["currency_code"],
    }


def _current_lines(
    db: Session,
    batch: ImportBatch,
    model: Any,
    keys: list[tuple[str, int]],
) -> dict[tuple[str, int], Any]:
    return {
        (line.document_number, line.line_number): line
        for line in db.scalars(
            select(model)
            .where(
                model.tenant_id == batch.tenant_id,
                model.source_system == batch.source_system,
                model.is_current,
                tuple_(model.document_number, model.line_number).in_(keys),
            )
            .with_for_update()
        )
    }


def _apply_lines(
    db: Session,
    batch: ImportBatch,
    model: Any,
    rows: list[Staged],
    build: Any,
    counts: Counts,
) -> None:
    table: Table = model.__table__
    keys = [(r.normalized["document_number"], r.normalized["line_number"]) for r in rows]
    current = _current_lines(db, batch, model, keys)
    new_rows: list[dict[str, Any]] = []
    retire: list[tuple[UUID, UUID]] = []
    for r, key in zip(rows, keys, strict=True):
        old = current.get(key)
        if old is not None and old.row_hash == r.row_hash:
            counts.unchanged += 1
            continue
        line_id = new_id()
        values = build(r)
        values.update(
            tenant_id=batch.tenant_id,
            id=line_id,
            import_batch_id=batch.id,
            source_row_number=r.row_number,
            source_system=batch.source_system,
            row_hash=r.row_hash,
            version=1 if old is None else old.version + 1,
        )
        new_rows.append(values)
        if old is None:
            counts.inserted += 1
        else:
            counts.changed += 1
            retire.append((old.id, line_id))
    claimed = claims_hook.claimed_line_ids(db, batch.tenant_id, [old for old, _ in retire])
    flagged = {new for old, new in retire if old in claimed}
    for values in new_rows:
        values["source_changed"] = values["id"] in flagged
    counts.source_changed += len(flagged)
    counts.changed_line_ids.extend(new for _, new in retire)
    _retire(db, table, retire)
    _insert_all(db, table, new_rows, ["tenant_id", "source_system", "document_number", "line_number"])


def load_sales(db: Session, batch: ImportBatch, rows: list[Staged], counts: Counts) -> None:
    """``rows`` are of one phase: invoices (phase 1) or credit notes (phase 2, originals loaded)."""
    if not rows:
        return
    originals = _original_lines(db, batch, rows)
    headers: dict[tuple[str, str], dict[str, Any]] = {}
    cn_originals: dict[tuple[str, str], set[UUID]] = {}
    for r in rows:
        n = r.normalized
        key = (n["doc_type"], n["document_number"])
        headers.setdefault(
            key,
            {
                "document_date": n["invoice_date"],
                "customer_code": n["customer_code"],
                "customer_id": n["customer_id"],
                "branch_code": n["branch_code"],
                "branch_id": n["branch_id"],
                "currency_code": n["currency_code"],
            },
        )
        orig = originals.get((n["original_document_number"], n["original_line_number"]))
        if orig is not None:
            cn_originals.setdefault(key, set()).add(orig[1])
    for key, docs in cn_originals.items():
        if len(docs) == 1:  # a credit note crediting exactly one invoice links to it
            headers[key]["original_document_id"] = str(next(iter(docs)))
    doc_ids = _documents(db, batch, SalesDocument, headers)

    def build(r: Staged) -> dict[str, Any]:
        n = r.normalized
        orig = originals.get((n["original_document_number"], n["original_line_number"]))
        return {
            "document_id": doc_ids[(n["doc_type"], n["document_number"])],
            "doc_type": n["doc_type"],
            "document_number": n["document_number"],
            "line_number": n["line_number"],
            "product_id": _uuid(n["product_id"]),
            "sku_raw": n["sku"],
            "manufacturer_id": _uuid(n["manufacturer_id"]),
            "customer_id": _uuid(n["customer_id"]),
            "description": n["description"],
            "quantity": _d(n["quantity"]),
            "uom": n["uom"],
            "unit_price": _d(n["unit_price"]),
            "unit_cost": _d(n["unit_cost"]),
            "replacement_cost": _d(n["replacement_cost"]),
            "extended_amount": _d(n["extended_amount"]),
            "currency_code": n["currency_code"],
            "invoice_date": _date(n["invoice_date"]),
            "ship_date": _date(n["ship_date"]),
            "spa_reference": n["spa_reference"],
            "project_reference": n["project_reference"],
            "original_line_id": None if orig is None else orig[0],
        }

    _apply_lines(db, batch, SalesDocumentLine, rows, build, counts)


def _original_lines(
    db: Session, batch: ImportBatch, rows: list[Staged]
) -> dict[tuple[str, int], tuple[UUID, UUID]]:
    """Credit-note originals by (document number, line) among current invoice lines -> (line, document)."""
    keys = {
        (r.normalized["original_document_number"], r.normalized["original_line_number"])
        for r in rows
        if r.normalized.get("original_document_number") is not None
    }
    if not keys:
        return {}
    found = db.execute(
        select(
            SalesDocumentLine.document_number,
            SalesDocumentLine.line_number,
            SalesDocumentLine.id,
            SalesDocumentLine.document_id,
        ).where(
            SalesDocumentLine.tenant_id == batch.tenant_id,
            SalesDocumentLine.source_system == batch.source_system,
            SalesDocumentLine.is_current,
            SalesDocumentLine.doc_type == "invoice",
            tuple_(SalesDocumentLine.document_number, SalesDocumentLine.line_number).in_(sorted(keys)),
        )
    )
    return {(d, ln): (line_id, doc_id) for d, ln, line_id, doc_id in found}


def load_purchases(db: Session, batch: ImportBatch, rows: list[Staged], counts: Counts) -> None:
    if not rows:
        return
    headers: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        n = r.normalized
        headers.setdefault(
            (n["doc_type"], n["document_number"]),
            {
                "document_date": n["document_date"],
                "manufacturer_code": n["manufacturer_code"],
                "manufacturer_id": n["manufacturer_id"],
                "po_number": n["po_number"],
                "currency_code": n["currency_code"],
            },
        )
    doc_ids = _documents(db, batch, PurchaseDocument, headers)

    def build(r: Staged) -> dict[str, Any]:
        n = r.normalized
        return {
            "document_id": doc_ids[(n["doc_type"], n["document_number"])],
            "doc_type": n["doc_type"],
            "document_number": n["document_number"],
            "line_number": n["line_number"],
            "product_id": _uuid(n["product_id"]),
            "sku_raw": n["sku"],
            "manufacturer_id": _uuid(n["manufacturer_id"]),
            "quantity": _d(n["quantity"]),
            "uom": n["uom"],
            "unit_cost": _d(n["unit_cost"]),
            "extended_amount": _d(n["extended_amount"]),
            "currency_code": n["currency_code"],
            "document_date": _date(n["document_date"]),
        }

    _apply_lines(db, batch, PurchaseDocumentLine, rows, build, counts)


def sales_lines_exist(
    db: Session, tenant_id: UUID, source_system: str, keys: list[tuple[str, int]]
) -> set[tuple[str, int]]:
    """Which ``(document number, line)`` exist as current invoice lines (credit-note originals check)."""
    if not keys:
        return set()
    found = db.execute(
        select(SalesDocumentLine.document_number, SalesDocumentLine.line_number).where(
            SalesDocumentLine.tenant_id == tenant_id,
            SalesDocumentLine.source_system == source_system,
            SalesDocumentLine.is_current,
            SalesDocumentLine.doc_type == "invoice",
            tuple_(SalesDocumentLine.document_number, SalesDocumentLine.line_number).in_(keys),
        )
    )
    return {(d, ln) for d, ln in found}
