"""Pure readers (S3.2) and encodings (S2 report requirement 2): bytes are built in the test."""

import codecs
import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from rebate.modules.files import UndecodableText, decode_text
from rebate.modules.ingestion import readers

TEXT = "invoice_no,customer_name,qty\nINV-1,Café Zoë,5\n"


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        (TEXT.encode("utf-8"), "utf-8"),
        (codecs.BOM_UTF8 + TEXT.encode("utf-8"), "utf-8-sig"),
        (TEXT.encode("cp1252"), "cp1252"),
        (codecs.BOM_UTF16_LE + TEXT.encode("utf-16-le"), "utf-16-le"),
        (codecs.BOM_UTF16_BE + TEXT.encode("utf-16-be"), "utf-16-be"),
    ],
)
def test_every_supported_encoding_decodes_to_the_same_rows(data: bytes, encoding: str) -> None:
    detected, text = decode_text(data)
    assert detected == encoding and text == TEXT
    recs = readers.records(readers.csv_source(data))
    assert recs.source.encoding == encoding
    assert recs.headers == ["invoice_no", "customer_name", "qty"]
    assert list(recs.rows) == [(2, {"invoice_no": "INV-1", "customer_name": "Café Zoë", "qty": "5"})]


@pytest.mark.parametrize(
    "data",
    [
        TEXT.encode("utf-16-le"),  # UTF-16 without BOM: NULs
        b"a,b\n\x81\x8d,\x00\n",  # undefined cp1252 bytes and NUL
        codecs.BOM_UTF16_LE + b"\x00\xd8",  # lone surrogate
    ],
)
def test_undecodable_content_has_a_clear_user_facing_error(data: bytes) -> None:
    with pytest.raises(UndecodableText) as err:
        decode_text(data)
    assert err.value.code == "encoding_unsupported" and "UTF-8" in err.value.message


@pytest.mark.parametrize(("sep", "name"), [(";", "semicolon"), ("\t", "tab"), ("|", "pipe"), (",", "comma")])
def test_delimiter_is_sniffed(sep: str, name: str) -> None:
    data = f"a{sep}b{sep}c\n1{sep}2,5{sep}x\n4{sep}5{sep}y\n".encode()
    src = readers.csv_source(data)
    assert src.delimiter == sep, name


def test_quoted_fields_and_leading_zeros_stay_text() -> None:
    data = b'sku,desc,qty\n00418,"Fuse, class J",1\n'
    rows = list(readers.records(readers.csv_source(data)).rows)
    assert rows == [(2, {"sku": "00418", "desc": "Fuse, class J", "qty": "1"})]


def test_malformed_csv() -> None:
    with pytest.raises(readers.ReadError) as err:
        list(readers.records(readers.csv_source(b'a,b\n"unterminated,1\n', ",")).rows)
    assert err.value.code == "malformed_csv"


def test_title_rows_are_skipped_and_empty_rows_dropped() -> None:
    data = b"Invoice register (SYNTHETIC)\nexported today\n\nInv #,Ln,Qty\nINV-1,1,5\n,,\nINV-2,1,6\n"
    recs = readers.records(readers.csv_source(data, ","))
    assert recs.header_row == 4 and recs.headers == ["Inv #", "Ln", "Qty"]
    assert [n for n, _ in recs.rows] == [5, 7]


def test_explicit_header_row_and_extra_cells_flag() -> None:
    data = b"x,y\na,b\n1,2,3\n"
    recs = readers.records(readers.csv_source(data, ","), header_row=2)
    assert recs.headers == ["a", "b"]
    rows = list(recs.rows)
    assert rows[0][1][readers.EXTRA_CELLS] == "1"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"a,A\n1,2\n", "duplicate_header"),
        (b"\n\n", "empty_file"),
    ],
)
def test_header_errors(data: bytes, code: str) -> None:
    with pytest.raises(readers.ReadError) as err:
        readers.records(readers.csv_source(data, ","))
    assert err.value.code == code


def _xlsx(build: object) -> bytes:
    wb = Workbook()
    build(wb.active)  # type: ignore[operator]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_cells_become_text_without_float_noise_and_formulas_are_not_evaluated() -> None:
    def build(ws: object) -> None:
        ws.append(["Title (SYNTHETIC)"])  # type: ignore[attr-defined]
        ws.append(["sku", "qty", "price", "date", "when", "flag", "formula"])  # type: ignore[attr-defined]
        ws.append(["00418", 1000, 0.1, date(2026, 6, 12), datetime(2026, 6, 12, 9, 30), True, "=1+1"])  # type: ignore[attr-defined]

    with readers.xlsx_source(_xlsx(build)) as src:
        recs = readers.records(src)
        rows = list(recs.rows)
    assert src.encoding == "xlsx" and src.sheet_name == "Sheet"
    assert recs.header_row == 2
    assert rows == [
        (
            3,
            {
                "sku": "00418",
                "qty": "1000",
                "price": "0.1",
                "date": "2026-06-12",
                "when": "2026-06-12T09:30:00",
                "flag": "TRUE",
                "formula": "",  # data_only: no cached value was saved, and nothing is evaluated
            },
        )
    ]


def test_xlsx_errors() -> None:
    with pytest.raises(readers.ReadError) as bad:
        readers.xlsx_source(b"not a zip").__enter__()
    assert bad.value.code == "unreadable_workbook"
    with (
        pytest.raises(readers.ReadError) as sheet,
        readers.xlsx_source(_xlsx(lambda ws: ws.append(["a"])), "Nope"),
    ):
        pass
    assert sheet.value.code == "sheet_not_found"


def test_cell_text() -> None:
    assert readers.cell_text(None) == ""
    assert readers.cell_text(88.0) == "88"
    assert readers.cell_text(1e-7) == "1e-07"
    assert readers.cell_text(False) == "FALSE"
    assert readers.cell_text(date(2026, 1, 2)) == "2026-01-02"
