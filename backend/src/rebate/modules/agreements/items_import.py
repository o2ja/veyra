"""SPA item bulk import (FR-AG-04, S4.4): a clean CSV/XLSX file -> ``payload.items`` of a draft.

Reuses the import pipeline's pure readers (header detection, encodings, XLSX without formulas) and
normalisers (exact decimals, codes, UoM synonyms). Synchronous and all-or-nothing: any row error rejects
the whole file with row-level details; nothing is saved. ``dry_run`` returns the parsed items without saving.
The items replace the draft's items as a new revision; the validator then resolves the SKUs.
"""

from contextlib import nullcontext
from typing import Any

from sqlalchemy.orm import Session

from rebate.core.config import Settings
from rebate.core.errors import ValidationFailed
from rebate.modules import files
from rebate.modules import ingestion as n
from rebate.modules.agreements import service
from rebate.modules.agreements.models import AgreementDraft
from rebate.modules.agreements.schemas import ITEM_FIELDS, MAX_ITEMS
from rebate.modules.audit import Action
from rebate.modules.masterdata import Who

MAX_REPORTED = 200
# canonical field -> header names recognised without an explicit mapping (normalised: lower case, single spaces)
ALIASES: dict[str, tuple[str, ...]] = {
    "sku": (
        "sku",
        "catalog #",
        "catalog number",
        "catalog no",
        "item",
        "item number",
        "part number",
        "part #",
    ),
    "reference_cost": ("reference cost", "into-stock cost", "into stock cost", "standard cost", "book cost"),
    "deviated_cost": ("deviated cost", "spa cost", "net cost", "approved cost"),
    "sell_price": ("sell price", "resale", "resale price", "sell"),
    "rate": ("rate", "margin %", "margin", "discount %"),
    "flat_amount": ("flat amount", "amount per unit", "rebate per unit"),
    "max_quantity": ("max quantity", "max qty", "quantity cap", "cap"),
    "claim_uom": ("claim uom", "approved uom", "uom", "unit of measure"),
}
COSTS = ("reference_cost", "deviated_cost", "sell_price", "flat_amount", "max_quantity")


def _mapping(headers: list[str], explicit: dict[str, str] | None) -> dict[str, str]:
    by_norm = {n.normalise_header(h): h for h in headers}
    if explicit is not None:
        missing = [col for col in explicit.values() if n.normalise_header(col) not in by_norm]
        if missing:
            raise ValidationFailed(
                "The mapping names columns the file does not have.",
                details=[{"field": "mapping", "code": "unknown_column", "message": c} for c in missing],
            )
        mapping = {f: by_norm[n.normalise_header(col)] for f, col in explicit.items()}
    else:
        mapping = {}
        for f, names in ALIASES.items():
            found = next((by_norm[a] for a in names if a in by_norm), None)
            if found is not None:
                mapping[f] = found
    if "sku" not in mapping:
        raise ValidationFailed(
            "No SKU column was found; send a mapping.",
            details=[{"field": "mapping.sku", "code": "required", "message": "Map the SKU column."}],
        )
    return mapping


def _value(f: str, text: str, rate_format: str) -> str | None:
    if not text.strip():
        return None
    if f == "sku":
        sku = n.canonical_code(text)
        if n.find_card_number(sku) or n.CARD_REMOVED in sku:
            raise n.NormError("card_number", "The cell looks like a card number.")
        return sku
    if f == "claim_uom":
        return n.canonical_uom(text)
    if f == "rate":
        if rate_format == "percent":
            value = n.parse_decimal(text.replace("%", ""), scale=4, int_digits=3) / 100
        else:
            value = n.parse_decimal(text, scale=6, int_digits=1)
        if not 0 <= value <= 1:
            raise n.NormError("out_of_range", "The rate must be between 0% and 100%.")
        return format(value.normalize(), "f")
    value = n.parse_decimal(text, scale=6, int_digits=12)
    if value < 0:
        raise n.NormError("out_of_range", "Must not be negative.")
    return format(value, "f") if value != 0 else "0"


def parse_items(
    data: bytes,
    file_name: str,
    *,
    mapping: dict[str, str] | None,
    rate_format: str,
    header_row: int | None,
    sheet: str | None,
) -> list[dict[str, Any]]:
    xlsx = file_name.lower().endswith(".xlsx")
    errors: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    try:
        source = n.xlsx_source(data, sheet) if xlsx else nullcontext(n.csv_source(data))
        with source as src:
            recs = n.records(src, header_row)
            cols = _mapping(recs.headers, mapping)
            for row, cells in recs.rows:
                if len(items) >= MAX_ITEMS:
                    raise ValidationFailed(f"At most {MAX_ITEMS} items per agreement.")
                item: dict[str, Any] = {f: None for f in ITEM_FIELDS}
                for f, col in cols.items():
                    try:
                        item[f] = _value(f, cells.get(col, ""), rate_format)
                    except n.NormError as exc:
                        errors.append({"field": f"row {row}.{col}", "code": exc.code, "message": exc.message})
                if item["sku"] is None:
                    errors.append(
                        {"field": f"row {row}.{cols['sku']}", "code": "required", "message": "SKU missing."}
                    )
                items.append(item)
    except n.ReadError as exc:  # UndecodableText is already a 422 DomainError
        raise ValidationFailed(
            exc.message, details=[{"field": "file_id", "code": exc.code, "message": exc.message}]
        ) from None
    if errors:
        raise ValidationFailed(
            f"{len(errors)} cell(s) could not be read; nothing was imported.", details=errors[:MAX_REPORTED]
        )
    if not items:
        raise ValidationFailed("The file contains no item rows.")
    return items


def import_items(
    db: Session, settings: Settings, who: Who, draft_id: Any, revision: int, body: Any
) -> tuple[list[dict[str, Any]], AgreementDraft | None]:
    draft = service.editable_draft(db, who, draft_id, revision)
    file = files.get(db, who.tenant_id, body.file_id)
    items = parse_items(
        files.read_clean(settings, file),
        file.original_name,
        mapping=body.mapping,
        rate_format=body.rate_format,
        header_row=body.header_row,
        sheet=body.sheet,
    )
    if body.dry_run:
        return items, None
    payload = {**draft.payload, "items": items}
    summary = f"Imported {len(items)} items from {file.original_name}"[:500]
    return items, service.revise(
        db,
        who,
        draft,
        payload,
        summary,
        Action.AGREEMENT_DRAFT_ITEMS_IMPORTED,
        item_count=len(items),
        source_file_id=file.id,
    )
