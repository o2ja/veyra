"""Claim file rendering: template columns -> CSV / XLSX bytes, with formula-injection protection.

SECURITY.md §4: any *text* cell beginning with ``=``, ``+``, ``-``, ``@``, tab or CR is prefixed with ``'``;
numbers and dates are written as typed values (never prefixed, so a negative amount stays a number), text as
strings. Headers and constants of a template are user input and are escaped like data.
"""

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]  # no stubs shipped
from openpyxl.cell import WriteOnlyCell  # type: ignore[import-untyped]

# source field -> kind. The only fields a template may read (no free-form expressions).
FIELDS: dict[str, str] = {
    "claim_reference": "text",
    "batch_number": "text",
    "manufacturer_code": "text",
    "manufacturer_name": "text",
    "agreement_number": "text",
    "document_number": "text",
    "line_number": "int",
    "invoice_date": "date",
    "ship_date": "date",
    "sku": "text",
    "product_description": "text",
    "manufacturer_part_number": "text",
    "customer_code": "text",
    "customer_name": "text",
    "spa_reference": "text",
    "project_reference": "text",
    "quantity": "decimal",
    "claim_uom": "text",
    "unit_amount": "decimal",
    "claim_amount": "decimal",
    "unit_price": "decimal",
    "extended_amount": "decimal",
    "currency_code": "text",
}
DATE_FORMATS = {
    "iso": ("%Y-%m-%d", "yyyy-mm-dd"),
    "us": ("%m/%d/%Y", "mm/dd/yyyy"),
    "eu": ("%d/%m/%Y", "dd/mm/yyyy"),
}
DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"header": "Claim reference", "source": "claim_reference"},
    {"header": "Invoice number", "source": "document_number"},
    {"header": "Line", "source": "line_number"},
    {"header": "Invoice date", "source": "invoice_date"},
    {"header": "SKU", "source": "sku"},
    {"header": "Customer", "source": "customer_name"},
    {"header": "Quantity", "source": "quantity"},
    {"header": "UoM", "source": "claim_uom"},
    {"header": "Unit amount", "source": "unit_amount"},
    {"header": "Claim amount", "source": "claim_amount"},
    {"header": "Agreement", "source": "agreement_number"},
    {"header": "SPA reference", "source": "spa_reference"},
]
MISSED_COLUMNS: list[dict[str, Any]] = [
    {"header": "Manufacturer", "source": "manufacturer_code"},
    *DEFAULT_COLUMNS[1:],
]
CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def column_error(column: Mapping[str, Any]) -> str | None:
    """Why a template column is invalid, or None. Shared by the API validation and the renderer."""
    source, constant, fmt = column.get("source"), column.get("constant"), column.get("format")
    if (source is None) == (constant is None):
        return "Give exactly one of source and constant."
    if source is None:
        return "A constant column has no format." if fmt is not None else None
    kind = FIELDS.get(source)
    if kind is None:
        return f"Unknown source field {source!r}."
    if fmt is None:
        return None
    if kind == "date" and fmt in DATE_FORMATS:
        return None
    if kind == "decimal" and fmt.startswith("decimal:") and fmt[8:] in ("0", "1", "2", "3", "4", "5", "6"):
        return None
    return f"Format {fmt!r} does not fit field {source!r}."


def safe_text(value: str) -> str:
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _decimal_text(value: Decimal, fmt: str | None) -> str:
    if fmt is not None:
        return str(value.quantize(Decimal(1).scaleb(-int(fmt[8:])), rounding=ROUND_HALF_UP))
    # exact stored value, at least two decimals, no padding zeros beyond that
    exp = value.normalize().as_tuple().exponent
    return str(value.quantize(Decimal("0.01")) if isinstance(exp, int) and exp > -2 else value.normalize())


def _number_format(fmt: str | None) -> str:
    return "0" if fmt == "decimal:0" else "0." + "0" * int(fmt[8:]) if fmt else "General"


def _csv_cell(value: Any, kind: str, fmt: str | None) -> Any:
    if value is None:
        return ""
    if kind == "text":
        return safe_text(str(value))
    if kind == "date":
        return value.strftime(DATE_FORMATS[fmt or "iso"][0])
    if kind == "decimal":
        return _decimal_text(value, fmt)
    return value


def _xlsx_cell(ws: Any, value: Any, kind: str, fmt: str | None) -> Any:
    if value is None:
        return None
    if kind == "text":
        return safe_text(str(value))
    cell = WriteOnlyCell(ws, value=value)
    if kind == "date":
        cell.number_format = DATE_FORMATS[fmt or "iso"][1]
    elif kind == "decimal":
        cell.number_format = _number_format(fmt)
        if fmt is not None:
            cell.value = value.quantize(Decimal(1).scaleb(-int(fmt[8:])), rounding=ROUND_HALF_UP)
    return cell


def render(
    fmt: str,
    columns: Sequence[Mapping[str, Any]],
    options: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
) -> bytes:
    """The file for ``rows`` (dicts keyed by ``FIELDS``). Raises ``ValueError`` for an invalid column."""
    for c in columns:
        if (why := column_error(c)) is not None:
            raise ValueError(why)
    include_header = options.get("include_header", True)
    headers = [safe_text(str(c["header"])) for c in columns]

    def spec(c: Mapping[str, Any]) -> tuple[str, str | None]:
        return ("text", None) if c.get("source") is None else (FIELDS[c["source"]], c.get("format"))

    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out, delimiter=options.get("delimiter", ","), lineterminator="\r\n")
        if include_header:
            w.writerow(headers)
        for row in rows:
            w.writerow(
                [
                    _csv_cell(c["constant"] if c.get("source") is None else row[c["source"]], *spec(c))
                    for c in columns
                ]
            )
        return out.getvalue().encode("utf-8")
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(str(options.get("sheet_name", "Claims"))[:31] or "Claims")
    if include_header:
        ws.append(headers)
    for row in rows:
        ws.append(
            [
                _xlsx_cell(ws, c["constant"] if c.get("source") is None else row[c["source"]], *spec(c))
                for c in columns
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = [
    "CONTENT_TYPES",
    "DATE_FORMATS",
    "DEFAULT_COLUMNS",
    "FIELDS",
    "MISSED_COLUMNS",
    "column_error",
    "render",
    "safe_text",
]
