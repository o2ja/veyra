"""Remittance lines proposed from a file or a PDF extraction, and the deterministic checks on them
(AI_PROCESSING.md §8, BUSINESS_LOGIC.md §10.1). Pure: no database, no clock.

A staged line is a plain dict with the ``remittance_extraction_staging`` columns plus ``issues`` (a list of
``{"field", "code", "message"}``). CSV/XLSX are read through the ingestion readers and a ``remittance`` mapping
(template or header-synonym suggestion); no AI is involved. Amounts are exact ``Decimal`` from text."""

from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from decimal import Decimal
from typing import Any

from rebate.modules import ingestion
from rebate.modules.ingestion import normalizers as n
from rebate.modules.ingestion import readers
from rebate.modules.ingestion.validators import FIELDS, Field, MappingInvalid, check_mapping, suggest_mapping

MAX_LINES = 50_000
TOLERANCE = Decimal("0.01")  # AI_PROCESSING.md §8: sum of lines = stated total (+-0.01)
Issue = dict[str, str]


class TooManyLines(ValueError):
    pass


def issue(field: str | None, code: str, message: str) -> Issue:
    return {"field": field or "", "code": code, "message": message}


def mask_cards(value: str) -> str:
    while (hit := ingestion.find_card_number(value)) is not None:
        value = value.replace(hit, ingestion.CARD_REMOVED)
    return value


def _parse(f: Field, text: str, fmt: n.NumberFormat, order: str) -> Any:
    match f.kind:
        case "int":
            return n.parse_int(text, minimum=0)
        case "qty":
            return n.parse_decimal(text, fmt, scale=6, int_digits=12)
        case "amount":
            return n.parse_decimal(text, fmt, scale=4, int_digits=14)
        case "date":
            return n.parse_date(text, order)
        case _:
            return n.text_value(text, max_length=f.max_length)


def staged_row(
    mapping: Mapping[str, str | None], raw: Mapping[str, str], fmt: n.NumberFormat, order: str
) -> dict[str, Any]:
    """One file row -> staging columns plus header facts (``document_number``/``document_date``) and issues."""
    out: dict[str, Any] = {"issues": []}
    for f in FIELDS["remittance"]:
        column = mapping.get(f.name)
        text = raw.get(column, "") if column else ""
        if not text.strip():
            out[f.name] = None
            if f.required:
                out["issues"].append(issue(f.name, "required", "A value is required."))
            continue
        try:
            out[f.name] = _parse(f, text, fmt, order)
        except n.NormError as exc:
            out[f.name] = None
            out["issues"].append(issue(f.name, exc.code, exc.message))
    out["raw"] = {k: mask_cards(v) for k, v in raw.items() if k != readers.EXTRA_CELLS}
    return out


def read_table(
    data: bytes, file_type: str, template: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, str | None], dict[str, Any]]:
    """Read a CSV/XLSX remittance. Returns ``(rows, mapping, header)``; ``header`` holds the document number/date
    when every row that states one agrees. Raises ``readers.ReadError``, ``MappingInvalid`` or ``TooManyLines``."""
    options = dict((template or {}).get("options") or {})
    source: AbstractContextManager[readers.Source] = (
        readers.xlsx_source(data, options.get("sheet"))
        if file_type == "xlsx"
        else nullcontext(readers.csv_source(data, options.get("delimiter")))
    )
    fmt = n.NumberFormat(options.get("decimal_separator", "."))
    order = options.get("date_order", "MDY")
    with source as src:
        recs = readers.records(src, options.get("header_row"))
        mapping = (template or {}).get("mapping") or suggest_mapping("remittance", recs.headers)
        check_mapping("remittance", mapping, options, recs.headers)
        rows: list[dict[str, Any]] = []
        for number, raw in recs.rows:
            if len(rows) >= MAX_LINES:
                raise TooManyLines(f"More than {MAX_LINES} lines.")
            row = staged_row(mapping, raw, fmt, order)
            row["source_row_number"] = number
            rows.append(row)
    return rows, mapping, _header(rows)


def _header(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("document_number", "document_date"):
        values = {r[key] for r in rows if r.get(key) is not None}
        out[key] = values.pop() if len(values) == 1 else None
    return out


def number_lines(rows: list[dict[str, Any]]) -> None:
    """``line_number``: the file's own numbering when it is complete and unique, else 1..n in file order."""
    own = [r.get("line_number") for r in rows]
    if all(v is not None and v >= 1 for v in own) and len(set(own)) == len(own):
        return
    for i, r in enumerate(rows, start=1):
        r["line_number"] = i


def row_issues(row: dict[str, Any]) -> list[Issue]:
    """Issues that stay with a row: parse issues plus a negative payment (AI_PROCESSING.md §8)."""
    out = list(row.get("issues") or [])
    paid = row.get("amount_paid")
    if paid is not None and paid < 0 and not any(i["code"] == "negative_amount" for i in out):
        out.append(issue("amount_paid", "negative_amount", "A payment line must not be negative."))
    return out


def document_issues(total: Decimal, amounts: list[Decimal | None]) -> list[Issue]:
    """Document-level arithmetic: lines present, every amount known, sum = stated total (+-0.01)."""
    if not amounts:
        return [issue(None, "no_lines", "The document has no lines.")]
    known = [a for a in amounts if a is not None]
    if len(known) != len(amounts):
        return [issue("amount_paid", "amount_missing", "Some lines have no amount.")]
    diff = sum(known, Decimal(0)) - total
    if abs(diff) > TOLERANCE:
        return [
            issue(
                "total_amount",
                "total_mismatch",
                f"The lines add up to {sum(known, Decimal(0))}, the document total is {total} (difference {diff}).",
            )
        ]
    return []


def blocking(rows: list[dict[str, Any]], total: Decimal) -> list[Issue]:
    """Everything that stops acceptance."""
    out = [i for r in rows for i in row_issues(r)]
    return out + document_issues(total, [r.get("amount_paid") for r in rows])


__all__ = [
    "FIELDS",
    "MAX_LINES",
    "MappingInvalid",
    "TooManyLines",
    "blocking",
    "document_issues",
    "issue",
    "mask_cards",
    "n",
    "number_lines",
    "read_table",
    "row_issues",
    "staged_row",
]
