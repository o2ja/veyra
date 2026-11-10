"""S6.1 text and table extraction (AI_PROCESSING.md §3, SECURITY.md §4). Pure: bytes in, result out.

PDF: ``pdfplumber`` per page; the page text is rebuilt from words so that every word keeps its character
offsets into the page text and its bounding box (evidence highlighting), font size and fill colour
(injection indicators). XLSX: ``openpyxl`` read-only, cached values only (no formula evaluation, no external
links), every non-empty cell kept with its address.

Failures are returned as :class:`ExtractionFailure` (the job turns them into ``needs_manual_entry`` with the
reason), never raised. OCR is not run here: a page with < 30 characters and an image on it is marked
``needs_ocr``; running OCR is the S6 job's decision (ocrmypdf/Tesseract are not in the image yet).
"""

import io
import math
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import openpyxl  # type: ignore[import-untyped]  # no stubs shipped; types-openpyxl not a dependency
import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect

MB = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    """Caps (AI_PROCESSING.md §3, SECURITY.md §4). Defaults are the documented ones where a doc names one."""

    max_pages: int = 60
    max_pdf_bytes: int = 25 * MB
    max_xlsx_bytes: int = 50 * MB
    max_chars: int = 500_000
    max_seconds: float = 60.0
    max_cells: int = 200_000
    max_xlsx_uncompressed_bytes: int = 200 * MB
    max_xlsx_compression_ratio: int = 100
    min_page_chars: int = 30  # below this (and an image present) the page needs OCR


DEFAULT_LIMITS = Limits()


class FailureReason(StrEnum):
    encrypted = "encrypted"
    corrupt = "corrupt"
    too_large = "too_large"
    too_many_pages = "too_many_pages"
    too_much_text = "too_much_text"
    timeout = "timeout"
    macro_enabled = "macro_enabled"
    zip_bomb = "zip_bomb"
    too_many_cells = "too_many_cells"


@dataclass(frozen=True)
class ExtractionFailure:
    reason: FailureReason
    detail: str


Colour = tuple[float, ...] | None


@dataclass(frozen=True)
class Word:
    text: str
    start: int  # offsets into Page.text
    end: int
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    colour: Colour


@dataclass(frozen=True)
class Page:
    number: int  # 1-based
    text: str
    words: tuple[Word, ...]
    tables: tuple[tuple[tuple[str | None, ...], ...], ...]
    width: float
    height: float
    image_count: int
    needs_ocr: bool
    text_method: str = "native"  # "ocr" once the S6 job has OCRed the page


@dataclass(frozen=True)
class PdfText:
    pages: tuple[Page, ...]

    @property
    def needs_ocr_pages(self) -> tuple[int, ...]:
        return tuple(p.number for p in self.pages if p.needs_ocr)


@dataclass(frozen=True)
class Cell:
    sheet: str
    address: str  # e.g. "A2"
    value: str


@dataclass(frozen=True)
class SheetText:
    cells: tuple[Cell, ...]
    _index: dict[tuple[str, str], Cell] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {(c.sheet, c.address): c for c in self.cells})

    def cell(self, sheet: str, address: str) -> Cell | None:
        return self._index.get((sheet, address.upper()))

    @property
    def sheets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.sheet for c in self.cells))

    def serialise(self) -> str:
        """Compact text form for the LLM: one ``sheet!address<TAB>value`` per line (§3.4)."""
        return "\n".join(f"{c.sheet}!{c.address}\t{c.value}" for c in self.cells)


Document = PdfText | SheetText
Clock = Callable[[], float]


def _colour(raw: Any) -> Colour:
    if isinstance(raw, list | tuple) and all(isinstance(v, int | float) for v in raw):
        return tuple(float(v) for v in raw)
    if isinstance(raw, int | float):
        return (float(raw),)
    return None  # pattern/unknown colour space


def _page_text(words: list[dict[str, Any]]) -> tuple[str, tuple[Word, ...]]:
    """Group words into lines (by ``top`` within 3pt), order lines top-down and words left-right."""
    lines: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    parts: list[str] = []
    out: list[Word] = []
    pos = 0
    for i, line in enumerate(lines):
        if i:
            parts.append("\n")
            pos += 1
        for j, w in enumerate(sorted(line, key=lambda w: w["x0"])):
            if j:
                parts.append(" ")
                pos += 1
            text = w["text"]
            out.append(
                Word(
                    text=text,
                    start=pos,
                    end=pos + len(text),
                    x0=float(w["x0"]),
                    top=float(w["top"]),
                    x1=float(w["x1"]),
                    bottom=float(w["bottom"]),
                    size=float(w.get("size") or 0),
                    colour=_colour(w.get("non_stroking_color")),
                )
            )
            parts.append(text)
            pos += len(text)
    return "".join(parts), tuple(out)


def _is_password_error(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, PDFPasswordIncorrect | PDFEncryptionError):
            return True
        if any(isinstance(a, PDFPasswordIncorrect | PDFEncryptionError) for a in getattr(seen, "args", ())):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def extract_pdf(
    data: bytes, limits: Limits = DEFAULT_LIMITS, clock: Clock = time.monotonic
) -> PdfText | ExtractionFailure:
    if len(data) > limits.max_pdf_bytes:
        return ExtractionFailure(FailureReason.too_large, f"PDF exceeds {limits.max_pdf_bytes} bytes")
    if not data.startswith(b"%PDF-"):
        return ExtractionFailure(FailureReason.corrupt, "not a PDF (missing %PDF- header)")
    started = clock()
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if pdf.doc.encryption is not None:
                return ExtractionFailure(FailureReason.encrypted, "PDF is encrypted or password-protected")
            if len(pdf.pages) == 0:
                return ExtractionFailure(FailureReason.corrupt, "PDF has no pages")
            if len(pdf.pages) > limits.max_pages:
                return ExtractionFailure(
                    FailureReason.too_many_pages,
                    f"document too long ({len(pdf.pages)} pages, cap {limits.max_pages}); enter manually or split",
                )
            pages: list[Page] = []
            total_chars = 0
            for number, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(extra_attrs=["size", "non_stroking_color"])
                text, word_objs = _page_text(words)
                tables = tuple(
                    tuple(tuple(cell for cell in row) for row in table) for table in page.extract_tables()
                )
                total_chars += len(text)
                if total_chars > limits.max_chars:
                    return ExtractionFailure(
                        FailureReason.too_much_text, f"text exceeds {limits.max_chars} chars"
                    )
                image_count = len(page.images)
                pages.append(
                    Page(
                        number=number,
                        text=text,
                        words=word_objs,
                        tables=tables,
                        width=float(page.width),
                        height=float(page.height),
                        image_count=image_count,
                        needs_ocr=len(text.strip()) < limits.min_page_chars and image_count > 0,
                    )
                )
                page.close()  # release the page's parsed objects; keeps memory flat on long documents
                if clock() - started > limits.max_seconds:
                    return ExtractionFailure(
                        FailureReason.timeout, f"extraction exceeded {limits.max_seconds}s"
                    )
    except Exception as exc:  # parser failure on untrusted input is an outcome, not a crash
        if _is_password_error(exc):
            return ExtractionFailure(FailureReason.encrypted, "PDF is encrypted or password-protected")
        return ExtractionFailure(FailureReason.corrupt, f"PDF could not be parsed ({type(exc).__name__})")
    return PdfText(pages=tuple(pages))


def _cell_text(value: object) -> str | None:
    """Values as text. Numbers keep their shortest exact form; floats never reach money code from here."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == dtime(0) else value.isoformat()
    if isinstance(value, date | dtime):
        return value.isoformat()
    if isinstance(value, float):
        # openpyxl hands numeric cells over as float; repr is the shortest string that round-trips,
        # i.e. the literal stored in the sheet XML. Decimal(repr) keeps it exact from here on.
        if not math.isfinite(value):
            return None
        return str(int(value)) if value.is_integer() else format(Decimal(repr(value)), "f")
    text = str(value)
    return text if text.strip() else None


def extract_xlsx(
    data: bytes, limits: Limits = DEFAULT_LIMITS, clock: Clock = time.monotonic
) -> SheetText | ExtractionFailure:
    if len(data) > limits.max_xlsx_bytes:
        return ExtractionFailure(FailureReason.too_large, f"XLSX exceeds {limits.max_xlsx_bytes} bytes")
    started = clock()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = {i.filename.lower() for i in infos}
            if any(n.endswith("vbaproject.bin") for n in names):
                return ExtractionFailure(
                    FailureReason.macro_enabled, "macro-enabled workbooks are not accepted"
                )
            uncompressed = sum(i.file_size for i in infos)
            compressed = sum(i.compress_size for i in infos) or 1
            if (
                uncompressed > limits.max_xlsx_uncompressed_bytes
                or uncompressed / compressed > limits.max_xlsx_compression_ratio
            ):
                return ExtractionFailure(
                    FailureReason.zip_bomb, "workbook expands beyond the allowed size/ratio"
                )
    except zipfile.BadZipFile:
        return ExtractionFailure(FailureReason.corrupt, "not a valid XLSX (zip) file")
    cells: list[Cell] = []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for c in row:
                        text = _cell_text(getattr(c, "value", None))
                        if text is None:
                            continue
                        cells.append(Cell(sheet=ws.title, address=c.coordinate, value=text))
                        if len(cells) > limits.max_cells:
                            return ExtractionFailure(
                                FailureReason.too_many_cells, f"more than {limits.max_cells} cells"
                            )
                    if clock() - started > limits.max_seconds:
                        return ExtractionFailure(
                            FailureReason.timeout, f"extraction exceeded {limits.max_seconds}s"
                        )
        finally:
            wb.close()
    except Exception as exc:  # parser failure on untrusted input is an outcome, not a crash
        return ExtractionFailure(FailureReason.corrupt, f"XLSX could not be parsed ({type(exc).__name__})")
    if sum(len(c.value) for c in cells) > limits.max_chars:
        return ExtractionFailure(FailureReason.too_much_text, f"text exceeds {limits.max_chars} chars")
    return SheetText(cells=tuple(cells))
