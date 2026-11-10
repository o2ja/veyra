"""Upload validation (SECURITY.md §4, FR-FI-02/03): allow-list per kind, extension + declared MIME +
magic bytes must agree, size limits, XLSX container checks (zip bomb, macros, encryption), CSV text in a
supported encoding (UTF-8 with or without BOM, Windows-1252, UTF-16 LE/BE with BOM; S3)."""

import codecs
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import IO

import magic

from rebate.core.config import Settings
from rebate.core.errors import DomainError

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Kinds a user may upload (exports are produced by the system) and the types each accepts.
KIND_TYPES: dict[str, frozenset[str]] = {
    "import": frozenset({"csv", "xlsx"}),
    "agreement": frozenset({"pdf", "xlsx"}),
    "remittance": frozenset({"pdf", "csv", "xlsx"}),
    "attachment": frozenset({"pdf", "csv", "xlsx"}),
}
# Browsers on Windows send application/vnd.ms-excel for .csv when Excel is installed.
DECLARED: dict[str, frozenset[str]] = {
    "csv": frozenset({"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"}),
    "xlsx": frozenset({XLSX}),
    "pdf": frozenset({"application/pdf"}),
}
DETECTED: dict[str, frozenset[str]] = {
    "csv": frozenset({"text/csv", "text/plain", "application/csv"}),
    "xlsx": frozenset({XLSX, "application/zip"}),
    "pdf": frozenset({"application/pdf"}),
}
CONTENT_TYPE: dict[str, str] = {"csv": "text/csv", "xlsx": XLSX, "pdf": "application/pdf"}

# Zip-bomb guard for XLSX containers.
MAX_ZIP_ENTRIES = 10_000
MAX_UNCOMPRESSED = 300 * 1024 * 1024
MAX_RATIO = 100


class UploadRejected(DomainError):
    code = "upload_rejected"
    status_code = 422
    message = "This file cannot be accepted."


class FileTooLarge(DomainError):
    code = "payload_too_large"
    status_code = 413
    message = "The file is too large."


def _reject(reason: str) -> UploadRejected:
    return UploadRejected(details=[{"field": "file", "code": reason, "message": UploadRejected.message}])


_UNSAFE = re.compile(r'[\x00-\x1f\x7f"\\/:*?<>|]')


def sanitise_filename(name: str) -> str:
    """Display/attachment name only (never used in storage keys): base name, ASCII, no quotes/controls."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    folded = unicodedata.normalize("NFKD", base)
    # accents fold to ASCII; any other non-ASCII character becomes "_" (never dropped, so "отчёт.csv"
    # keeps its extension and stays recognisable as one name)
    ascii_name = "".join(ch if ch.isascii() else "_" for ch in folded if not unicodedata.combining(ch))
    cleaned = _UNSAFE.sub("_", ascii_name).strip(" .")
    return (cleaned or "file")[-255:]


@dataclass(frozen=True)
class Checked:
    extension: str
    detected_mime: str
    content_type: str


def max_bytes(settings: Settings, extension: str) -> int:
    mb = settings.upload_max_mb_pdf if extension == "pdf" else settings.upload_max_mb_spreadsheet
    return mb * 1024 * 1024


def check_name(kind: str, filename: str, declared_mime: str | None) -> str:
    """Kind allowed, extension allowed for the kind, declared MIME agrees. Returns the extension."""
    if kind not in KIND_TYPES:
        raise _reject("kind_not_uploadable")
    _, dot, ext = filename.lower().rpartition(".")
    if not dot or ext not in KIND_TYPES[kind]:
        raise _reject("type_not_allowed")
    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared not in DECLARED[ext]:
        raise _reject("declared_type_mismatch")
    return ext


def check_content(ext: str, head: bytes, body: IO[bytes]) -> Checked:
    """Magic bytes must match the extension; XLSX containers must be a safe, macro-free workbook."""
    detected = magic.from_buffer(head, mime=True)
    if detected not in DETECTED[ext]:
        raise _reject("content_type_mismatch")
    if ext == "csv":
        body.seek(0)
        try:
            decode_text(body.read())  # bounded by the size limit checked before
        except UndecodableText as exc:
            raise UploadRejected(
                details=[{"field": "file", "code": exc.code, "message": exc.message}]
            ) from None
        finally:
            body.seek(0)
    if ext == "xlsx":
        _check_xlsx(body)
    return Checked(ext, detected, CONTENT_TYPE[ext])


class UndecodableText(DomainError):
    """CSV bytes that are not text in a supported encoding (user-facing; also raised by the importer)."""

    code = "encoding_unsupported"
    status_code = 422
    message = (
        "The file is not readable text. Save it as CSV in UTF-8 (recommended), Windows-1252 (ANSI) "
        "or Unicode (UTF-16 with byte-order mark)."
    )


TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "utf-16-le", "utf-16-be")


def decode_text(data: bytes) -> tuple[str, str]:
    """Detect and decode CSV bytes: ``(encoding, text)``. A byte-order mark decides (UTF-8, UTF-16 LE/BE);
    without one, strict UTF-8 is tried first, then Windows-1252 (the ANSI code page of US Excel). Text
    with NUL characters (binary content, or UTF-16 without a byte-order mark) is rejected."""
    if data.startswith(codecs.BOM_UTF8):
        encoding, payload = "utf-8-sig", data[len(codecs.BOM_UTF8) :]
    elif data.startswith(codecs.BOM_UTF16_LE):
        encoding, payload = "utf-16-le", data[2:]
    elif data.startswith(codecs.BOM_UTF16_BE):
        encoding, payload = "utf-16-be", data[2:]
    else:
        encoding, payload = "utf-8", data
    try:
        text = payload.decode("utf-8" if encoding == "utf-8-sig" else encoding)
    except UnicodeDecodeError:
        if encoding != "utf-8":
            raise UndecodableText() from None
        try:
            encoding, text = "cp1252", payload.decode("cp1252")
        except UnicodeDecodeError:
            raise UndecodableText() from None
    if "\x00" in text:
        raise UndecodableText()
    return encoding, text


def detect_text_encoding(data: bytes) -> str:
    return decode_text(data)[0]


# Active content: VBA (vbaProject.bin), Excel 4 macro sheets, OLE embeddings, ActiveX, external links
ACTIVE_PARTS = ("xl/macrosheets/", "xl/embeddings/", "xl/activex/", "xl/externallinks/")


def _check_xlsx(body: IO[bytes]) -> None:
    body.seek(0)
    try:
        with zipfile.ZipFile(body) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise _reject("archive_too_complex")
            total = 0
            names: set[str] = set()
            for info in infos:
                if info.filename in names:  # readers disagree on which duplicate wins
                    raise _reject("archive_too_complex")
                if info.flag_bits & 0x1:
                    raise _reject("encrypted")
                total += info.file_size
                if total > MAX_UNCOMPRESSED:
                    raise _reject("archive_too_large")
                if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                    raise _reject("archive_ratio")
                names.add(info.filename)
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise _reject("not_a_workbook")
            lowered = [n.lower() for n in names]
            if any(n.endswith("vbaproject.bin") or n.startswith(ACTIVE_PARTS) for n in lowered):
                raise _reject("macros")
            if zf.getinfo("[Content_Types].xml").file_size > 65536:
                raise _reject("not_a_workbook")
            with zf.open("[Content_Types].xml") as ct:  # bounded read (no whole-entry inflate)
                content_types = ct.read(65536).lower()
            if b"macroenabled" in content_types or b"macrosheet" in content_types:
                raise _reject("macros")
    except zipfile.BadZipFile:
        raise _reject("content_type_mismatch") from None
    finally:
        body.seek(0)
