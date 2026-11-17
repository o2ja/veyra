"""Claim file rendering (S7.3): column mapping, number/date formats and formula-injection protection
(SECURITY.md §4). Expected cells are written out by hand."""

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from rebate.modules.claims import exporting

ROW: dict[str, Any] = {
    "claim_reference": "CL-2026-000001",
    "sku": "-DASH-1",
    "customer_name": "=cmd|' /C calc'!A0",
    "document_number": "INV-1",
    "line_number": 1,
    "invoice_date": date(2026, 3, 9),
    "quantity": Decimal("100.000000"),
    "unit_amount": Decimal("22.000000"),
    "claim_amount": Decimal("-330.0000"),
    "spa_reference": None,
}
COLS: list[dict[str, Any]] = [
    {"header": "Ref", "source": "claim_reference"},
    {"header": '=HYPERLINK("http://x")', "source": "sku"},
    {"header": "Customer", "source": "customer_name"},
    {"header": "Line", "source": "line_number"},
    {"header": "Date", "source": "invoice_date", "format": "us"},
    {"header": "Qty", "source": "quantity", "format": "decimal:2"},
    {"header": "Amount", "source": "claim_amount"},
    {"header": "SPA", "source": "spa_reference"},
    {"header": "Const", "constant": "+1"},
]


def test_csv_escapes_text_but_not_numbers() -> None:
    out = exporting.render("csv", COLS, {}, [ROW]).decode()
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == [
        "Ref",
        '\'=HYPERLINK("http://x")',
        "Customer",
        "Line",
        "Date",
        "Qty",
        "Amount",
        "SPA",
        "Const",
    ]
    assert rows[1] == [
        "CL-2026-000001",
        "'-DASH-1",  # text starting with "-" is prefixed
        "'=cmd|' /C calc'!A0",  # text starting with "=" is prefixed
        "1",
        "03/09/2026",
        "100.00",
        "-330.00",  # a negative amount is a number: never prefixed
        "",
        "'+1",  # constants are template input: escaped like data
    ]


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_every_dangerous_lead_character_is_prefixed(lead: str) -> None:
    assert exporting.safe_text(f"{lead}x") == f"'{lead}x"
    assert exporting.safe_text("x=") == "x="
    assert exporting.safe_text("  =cmd") == "'  =cmd"  # leading spaces must not hide the formula
    assert exporting.safe_text("") == ""


def test_xlsx_has_typed_numbers_and_escaped_text() -> None:
    data = exporting.render("xlsx", COLS, {"sheet_name": "VOLT"}, [ROW])
    ws = load_workbook(io.BytesIO(data)).active
    assert ws.title == "VOLT"
    header, row = list(ws.iter_rows(values_only=False))
    assert header[1].value == '\'=HYPERLINK("http://x")' and header[1].data_type == "s"
    assert row[1].value == "'-DASH-1" and row[2].value == "'=cmd|' /C calc'!A0"
    assert row[3].data_type == "n" and row[3].value == 1
    assert row[4].is_date and row[4].value.date() == date(2026, 3, 9) and row[4].number_format == "mm/dd/yyyy"
    assert row[5].data_type == "n" and Decimal(str(row[5].value)) == Decimal("100.00")
    assert row[6].data_type == "n" and Decimal(str(row[6].value)) == Decimal("-330")
    assert row[7].value is None
    assert row[8].value == "'+1"


def test_money_default_keeps_exact_value_with_two_decimals_minimum() -> None:
    cols = [{"header": "A", "source": "claim_amount"}]
    for stored, text in (
        ("2200.0000", "2200.00"),
        ("0.5000", "0.50"),
        ("8.9600", "8.96"),
        ("1.23456", "1.23456"),
    ):
        out = exporting.render("csv", cols, {"include_header": False}, [{"claim_amount": Decimal(stored)}])
        assert out.decode().strip() == text


def test_decimal_format_pads_but_never_rounds() -> None:
    """A claim file must show the frozen values exactly, so its total ties out to the batch (review PR #15)."""
    cols = [{"header": "A", "source": "quantity", "format": "decimal:3"}]
    out = exporting.render("csv", cols, {"include_header": False}, [{"quantity": Decimal("2.5")}])
    assert out.decode().strip() == "2.500"
    cols = [{"header": "A", "source": "unit_amount", "format": "decimal:2"}]
    for fmt in ("csv", "xlsx"):
        with pytest.raises(exporting.FormatWouldRound):
            exporting.render(fmt, cols, {"include_header": False}, [{"unit_amount": Decimal("0.125")}])


@pytest.mark.parametrize("source", ["claim_amount", "unit_amount", "unit_price", "extended_amount"])
@pytest.mark.parametrize("fmt", ["decimal:0", "decimal:1"])
def test_money_fields_refuse_formats_below_two_decimals(source: str, fmt: str) -> None:
    assert exporting.column_error({"header": "x", "source": source, "format": fmt}) is not None
    assert exporting.column_error({"header": "x", "source": source, "format": "decimal:2"}) is None


def test_xml_illegal_control_characters_are_replaced() -> None:
    cols = [{"header": "Name", "source": "customer_name"}]
    rows = [{"customer_name": "Acme\x00\x07 Ltd\x1f"}]
    out = exporting.render("csv", cols, {"include_header": False}, rows)
    assert out.decode().strip() == "Acme   Ltd"
    exporting.render("xlsx", cols, {}, rows)  # openpyxl raises IllegalCharacterError without the guard


@pytest.mark.parametrize(
    "column",
    [
        {"header": "x"},  # neither source nor constant
        {"header": "x", "source": "claim_amount", "constant": "1"},
        {"header": "x", "source": "nope"},
        {"header": "x", "source": "claim_amount", "format": "us"},
        {"header": "x", "source": "invoice_date", "format": "decimal:2"},
        {"header": "x", "source": "quantity", "format": "decimal:9"},
        {"header": "x", "constant": "1", "format": "iso"},
    ],
)
def test_invalid_columns_are_refused(column: dict[str, Any]) -> None:
    assert exporting.column_error(column) is not None
    with pytest.raises(ValueError):
        exporting.render("csv", [column], {}, [])


def test_delimiter_and_header_options() -> None:
    cols = [{"header": "A", "source": "claim_reference"}, {"header": "B", "source": "sku"}]
    out = exporting.render("csv", cols, {"delimiter": ";", "include_header": False}, [ROW])
    assert out.decode() == "CL-2026-000001;'-DASH-1\r\n"
