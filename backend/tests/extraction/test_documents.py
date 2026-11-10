"""S6.1 text/table extraction: PDFs (multipage, positions, OCR decision, encrypted, corrupt, caps) and XLSX."""

import io
import itertools
import zipfile
from datetime import datetime

import openpyxl  # type: ignore[import-untyped]
import pytest

from rebate.modules.extraction import (
    ExtractionFailure,
    FailureReason,
    Limits,
    PdfText,
    SheetText,
    extract_pdf,
    extract_xlsx,
)
from tests.extraction.golden import AGREEMENTS, document_bytes
from tests.extraction.pdfgen import Run, build_pdf


def _ok_pdf(data: bytes, limits: Limits | None = None) -> PdfText:
    result = extract_pdf(data, limits or Limits())
    assert isinstance(result, PdfText), result
    return result


def _failure(result: object) -> FailureReason:
    assert isinstance(result, ExtractionFailure), result
    return result.reason


def test_every_golden_pdf_extracts_with_its_page_count() -> None:
    pages = {p.name: len(_ok_pdf(p.read_bytes()).pages) for p in AGREEMENTS.glob("*.pdf")}
    assert len(pages) == 6
    # TEST_DATA_SPEC.md §2.2: only the Coppermark table spans two pages
    assert pages.pop("SYNTHETIC_spa_coppermark_multipage_SPA-COPR-2026-220.pdf") == 2
    assert set(pages.values()) == {1}


def test_multipage_table_rows_land_on_their_pages() -> None:
    doc = _ok_pdf(document_bytes("SYNTHETIC_spa_coppermark_multipage_SPA-COPR-2026-220.pdf"))
    p1, p2 = doc.pages
    assert (p1.number, p2.number) == (1, 2)
    assert "WIRE-01-CU 0.021" in p1.text and "WIRE-32-CU 0.052" in p1.text
    assert "WIRE-33-CU 0.053" in p2.text and "WIRE-48-CU 0.068" in p2.text
    assert "WIRE-33-CU" not in p1.text
    assert p1.tables and p2.tables  # pdfplumber detects the item table on both pages


def test_word_offsets_and_boxes_point_into_the_page_text() -> None:
    doc = _ok_pdf(build_pdf([[Run("Standard cost 64.50", x=100, y=700, size=10)]]))
    page = doc.pages[0]
    assert page.text == "Standard cost 64.50"
    words = {w.text: w for w in page.words}
    w = words["64.50"]
    assert page.text[w.start : w.end] == "64.50"
    assert 100 < w.x0 < w.x1 < 612 and w.top < w.bottom
    assert w.size == 10 and w.colour == (0.0, 0.0, 0.0)
    assert page.text_method == "native" and not page.needs_ocr


def test_lines_are_rebuilt_top_down() -> None:
    doc = _ok_pdf(build_pdf([[Run("second line", y=600), Run("first line", y=700)]]))
    assert doc.pages[0].text == "first line\nsecond line"


def test_page_without_text_layer_but_with_image_needs_ocr() -> None:
    doc = _ok_pdf(
        build_pdf(
            [[Run("This page has a real text layer with enough characters.")], []], image_pages=frozenset({2})
        )
    )
    assert [p.needs_ocr for p in doc.pages] == [False, True]
    assert doc.needs_ocr_pages == (2,)
    assert doc.pages[1].image_count == 1


def test_blank_page_without_image_does_not_need_ocr() -> None:
    assert _ok_pdf(build_pdf([[]])).needs_ocr_pages == ()


def test_password_protected_pdf_is_an_explicit_failure() -> None:
    data = build_pdf([[Run("secret terms 12.00")]], user_password=b"open-sesame")
    assert _failure(extract_pdf(data)) is FailureReason.encrypted


def test_owner_password_only_pdf_is_still_treated_as_encrypted() -> None:
    # opens with the empty user password; AI_PROCESSING.md §3.3 still routes encrypted PDFs to manual entry
    data = build_pdf([[Run("secret terms 12.00")]], user_password=b"")
    assert _failure(extract_pdf(data)) is FailureReason.encrypted


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"PK\x03\x04 not a pdf",
        b"%PDF-1.4\n" + bytes(range(256)) * 8,
        document_bytes("SYNTHETIC_spa_voltara_SPA-VOLT-2026-884.pdf")[:400],
    ],
    ids=["empty", "not-pdf", "garbage-body", "truncated"],
)
def test_corrupt_pdf_is_an_explicit_failure(data: bytes) -> None:
    assert _failure(extract_pdf(data)) is FailureReason.corrupt


def test_page_cap() -> None:
    data = build_pdf([[Run(f"page {i}")] for i in range(3)])
    assert len(_ok_pdf(data, Limits(max_pages=3)).pages) == 3
    assert _failure(extract_pdf(data, Limits(max_pages=2))) is FailureReason.too_many_pages


def test_default_page_cap_is_60() -> None:
    data = build_pdf([[Run("p")] for _ in range(61)])
    failure = extract_pdf(data)
    assert _failure(failure) is FailureReason.too_many_pages
    assert isinstance(failure, ExtractionFailure) and "enter manually or split" in failure.detail


def test_file_size_cap() -> None:
    data = build_pdf([[Run("small")]])
    assert _failure(extract_pdf(data, Limits(max_pdf_bytes=len(data) - 1))) is FailureReason.too_large
    assert Limits().max_pdf_bytes == 25 * 1024 * 1024  # SECURITY.md §4


def test_character_cap() -> None:
    data = build_pdf([[Run("x" * 50)]])
    assert _failure(extract_pdf(data, Limits(max_chars=49))) is FailureReason.too_much_text


def test_time_cap_uses_the_injected_clock() -> None:
    ticks = itertools.count(0, 10)  # every reading is 10 s later
    data = build_pdf([[Run("a")], [Run("b")]])
    result = extract_pdf(data, Limits(max_seconds=15), clock=lambda: float(next(ticks)))
    assert _failure(result) is FailureReason.timeout


# ---- XLSX -------------------------------------------------------------------------------------------


def _xlsx(fill: object = None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Items"
    ws["A1"] = "SYNTHETIC header"
    ws["B2"] = 0.021
    ws["C2"] = 100.0
    ws["D2"] = datetime(2026, 3, 1)
    ws["E2"] = "=1+1"
    ws["F2"] = True
    ws["G2"] = 42
    if callable(fill):
        fill(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ok_xlsx(data: bytes, limits: Limits | None = None) -> SheetText:
    result = extract_xlsx(data, limits or Limits())
    assert isinstance(result, SheetText), result
    return result


def test_golden_xlsx_cells_keep_their_addresses() -> None:
    doc = _ok_xlsx(document_bytes("SYNTHETIC_spa_brightline_items.xlsx"))
    assert doc.sheets == ("SPA Items",)
    a2 = doc.cell("SPA Items", "A2")
    assert a2 is not None and "SPA-BRIT-2026-031" in a2.value
    assert [doc.cell("SPA Items", a).value for a in ("A4", "A5", "C5", "D5")] == [  # type: ignore[union-attr]
        "Catalog #",
        "LED-2X4-40W",
        "EA",
        "12",
    ]
    assert "SPA Items!A5\tLED-2X4-40W" in doc.serialise().splitlines()


def test_xlsx_values_are_text_and_formulas_are_not_evaluated() -> None:
    doc = _ok_xlsx(_xlsx())
    values = {c.address: c.value for c in doc.cells}
    assert values == {
        "A1": "SYNTHETIC header",
        "B2": "0.021",
        "C2": "100",
        "D2": "2026-03-01",
        "F2": "TRUE",
        "G2": "42",
    }
    assert "E2" not in values  # no cached value, and the formula is never evaluated or exposed


def test_macro_enabled_workbook_is_rejected() -> None:
    buf = io.BytesIO(_xlsx())
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("xl/vbaProject.bin", b"\x00" * 16)
    assert _failure(extract_xlsx(buf.getvalue())) is FailureReason.macro_enabled


def test_zip_bomb_guard() -> None:
    buf = io.BytesIO(_xlsx())
    with zipfile.ZipFile(buf, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/media/pad.bin", b"\x00" * (5 * 1024 * 1024))
    assert _failure(extract_xlsx(buf.getvalue())) is FailureReason.zip_bomb


@pytest.mark.parametrize("data", [b"", b"not a zip", b"PK\x03\x04garbage"], ids=["empty", "text", "bad-zip"])
def test_corrupt_xlsx_is_an_explicit_failure(data: bytes) -> None:
    assert _failure(extract_xlsx(data)) is FailureReason.corrupt


def test_zip_that_is_not_a_workbook_is_corrupt() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "SYNTHETIC")
    assert _failure(extract_xlsx(buf.getvalue())) is FailureReason.corrupt


def test_xlsx_caps() -> None:
    data = _xlsx()
    assert _failure(extract_xlsx(data, Limits(max_cells=3))) is FailureReason.too_many_cells
    assert _failure(extract_xlsx(data, Limits(max_xlsx_bytes=len(data) - 1))) is FailureReason.too_large
    assert _failure(extract_xlsx(data, Limits(max_chars=10))) is FailureReason.too_much_text
    ticks = itertools.count(0, 10)
    assert (
        _failure(extract_xlsx(data, Limits(max_seconds=5), clock=lambda: float(next(ticks))))
        is FailureReason.timeout
    )
