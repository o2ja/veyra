"""Pure file readers: bytes in, rows of **text** cells out (FR-IM-02, SECURITY.md §4 parsing rules).

* CSV: encoding detected by ``files.decode_text`` (BOM, strict UTF-8, Windows-1252; UTF-16 with BOM),
  delimiter sniffed among ``, ; TAB |`` unless given.
* XLSX: openpyxl read-only, ``data_only`` (the cached values Excel saved; formulas are never evaluated,
  macros and external links are never loaded; defusedxml guards the XML). Number cells become their
  shortest round-trip text (``repr``), dates become ISO text, so no value is typed or coerced here.
* Header row: given, or the first of the first 30 rows whose filled cells reach 60% of the widest row
  there (title rows above the header are skipped).

Rows are streamed (generators), so memory does not grow with the row count. Row numbers are 1-based
and count the header and skipped rows: the CSV record / spreadsheet row a user sees in the source file
(lineage for every staged and loaded line).
"""

import csv
import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from itertools import chain

from openpyxl import load_workbook  # type: ignore[import-untyped]

from rebate.modules.files import decode_text

DELIMITERS = (",", ";", "\t", "|")
MAX_COLUMNS = 200
HEADER_SCAN_ROWS = 30
MAX_CELL = 5000
EXTRA_CELLS = "\x1fextra"  # marker key: the row had more filled cells than the header


class ReadError(ValueError):
    """The file cannot be read as a table (user-facing message, stable code)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


Row = tuple[int, list[str]]


@dataclass
class Source:
    encoding: str  # utf-8 | utf-8-sig | cp1252 | utf-16-le | utf-16-be | xlsx
    delimiter: str | None
    sheet_name: str | None
    rows: Iterator[Row]


@dataclass
class Records:
    source: Source
    header_row: int
    headers: list[str]
    rows: Iterator[tuple[int, dict[str, str]]]  # (row number, header -> text); empty rows dropped


def csv_source(data: bytes, delimiter: str | None = None) -> Source:
    encoding, text = decode_text(data)  # raises UndecodableText (user-facing)
    if delimiter is not None and delimiter not in DELIMITERS:
        raise ReadError("invalid_delimiter", "Unsupported delimiter.")
    delim = delimiter or _sniff(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim, strict=True)

    def rows() -> Iterator[Row]:
        try:
            yield from enumerate(reader, start=1)
        except csv.Error:
            raise ReadError("malformed_csv", "The CSV file is malformed (unbalanced quotes).") from None

    return Source(encoding, delim, None, rows())


def _sniff(text: str) -> str:
    sample = "\n".join(text[:65536].splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        return ","


def cell_text(value: object) -> str:
    """openpyxl value -> text, never via float arithmetic."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        text = repr(value)  # shortest text that round-trips to the stored double
        return text[:-2] if text.endswith(".0") else text
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


@contextmanager
def xlsx_source(data: bytes, sheet: str | None = None) -> Iterator[Source]:
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception:  # openpyxl raises many types for broken containers
        raise ReadError("unreadable_workbook", "The workbook cannot be read.") from None
    try:
        if sheet is not None and sheet not in wb.sheetnames:
            raise ReadError("sheet_not_found", "The workbook has no sheet with that name.")
        ws = wb[sheet] if sheet is not None else wb.worksheets[0]
        rows = (
            (number, [cell_text(v) for v in values[:MAX_COLUMNS]])
            for number, values in enumerate(ws.iter_rows(values_only=True), start=1)
        )
        yield Source("xlsx", None, ws.title, rows)
    finally:
        wb.close()


def _filled(cells: list[str]) -> int:
    return sum(1 for c in cells if c.strip())


def detect_header_row(head: list[Row]) -> int:
    """Row number of the header: first row reaching 60% of the widest of the first rows."""
    widest = max((_filled(cells) for _, cells in head), default=0)
    if widest == 0:
        raise ReadError("empty_file", "The file contains no data.")
    return next(n for n, cells in head if _filled(cells) >= max(1, round(widest * 0.6)))


def records(source: Source, header_row: int | None = None) -> Records:
    head: list[Row] = []
    for row in source.rows:
        head.append(row)
        if len(head) >= HEADER_SCAN_ROWS and (header_row is None or row[0] >= header_row):
            break
    number = header_row or detect_header_row(head)
    found = [cells for n, cells in head if n == number]
    if not found:
        raise ReadError("no_header", "The header row is outside the file.")
    raw = found[0]
    while raw and not raw[-1].strip():
        raw = raw[:-1]
    if not raw:
        raise ReadError("no_header", "The header row is empty.")
    if len(raw) > MAX_COLUMNS:
        raise ReadError("too_many_columns", f"At most {MAX_COLUMNS} columns are supported.")
    headers = [" ".join(h.split()) or f"column_{i + 1}" for i, h in enumerate(raw)]
    lowered = [h.lower() for h in headers]
    if len(set(lowered)) != len(lowered):
        raise ReadError("duplicate_header", "Two columns have the same header; rename one.")
    rest = chain((r for r in head if r[0] > number), source.rows)
    return Records(source, number, headers, _dicts(headers, rest))


def _dicts(headers: list[str], rows: Iterator[Row]) -> Iterator[tuple[int, dict[str, str]]]:
    width = len(headers)
    for number, cells in rows:
        if not any(c.strip() for c in cells):
            continue
        values = {h: (cells[i] if i < len(cells) else "")[:MAX_CELL] for i, h in enumerate(headers)}
        if any(c.strip() for c in cells[width:]):
            values[EXTRA_CELLS] = "1"
        yield number, values
