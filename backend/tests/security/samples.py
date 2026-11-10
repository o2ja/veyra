"""Synthetic upload samples, built at test time (nothing malicious is stored in the repository)."""

import base64
import io
import zipfile

CSV = b"invoice,amount\nINV-1,100.00\nINV-2,250.50\n"  # SYNTHETIC
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
EXE = (
    b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    + b"\x00" * 48
    + b"PE\x00\x00"
    + b"\x00" * 200
)

_CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    b'<Default Extension="xml" ContentType="application/xml"/>'
    b'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    b"</Types>"
)
_MACRO_TYPES = _CONTENT_TYPES.replace(
    b"spreadsheetml.sheet.main+xml", b"ms-excel.sheet.macroEnabled.main+xml"
)


def xlsx(*, macros: bool = False, bomb_mb: int = 0, marker: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _MACRO_TYPES if macros else _CONTENT_TYPES)
        zf.writestr("_rels/.rels", b"<Relationships/>")
        zf.writestr("xl/workbook.xml", b"<workbook>" + marker + b"</workbook>")
        zf.writestr("xl/worksheets/sheet1.xml", b"<worksheet/>")
        if macros:
            zf.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0" + b"\x00" * 512)
        if bomb_mb:
            zf.writestr("xl/sharedStrings.xml", b"\x00" * (bomb_mb * 1024 * 1024))
    return buf.getvalue()


def eicar() -> bytes:
    """The standard anti-virus test string, assembled at runtime (it is harmless by definition)."""
    parts = [
        "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJ",
        "Q0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo=",
    ]
    return base64.b64decode("".join(parts))
