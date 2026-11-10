"""Minimal hand-written PDFs for extraction tests (no PDF writer is a dependency). SYNTHETIC content only.

Supports text runs (font size, RGB fill), an image per page, and the PDF standard security handler
(V1/R2, RC4-40, ISO 32000-1 §7.6.3) with a user and an owner password.
"""

import hashlib
from dataclasses import dataclass

PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
FILE_ID = bytes(range(16))
P_FLAGS = -4


@dataclass(frozen=True)
class Run:
    text: str
    x: float = 72
    y: float = 700
    size: float = 10
    rgb: tuple[float, float, float] = (0, 0, 0)


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _pad(pw: bytes) -> bytes:
    return (pw + PAD)[:32]


def _security(user_pw: bytes, owner_pw: bytes) -> tuple[bytes, bytes, bytes]:
    """(O, U, file key) for R2 (Algorithms 2, 3, 4)."""
    o = _rc4(hashlib.md5(_pad(owner_pw), usedforsecurity=False).digest()[:5], _pad(user_pw))
    key = hashlib.md5(
        _pad(user_pw) + o + P_FLAGS.to_bytes(4, "little", signed=True) + FILE_ID, usedforsecurity=False
    ).digest()[:5]
    return o, _rc4(key, PAD), key


def _escape(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def build_pdf(
    pages: list[list[Run]],
    *,
    image_pages: frozenset[int] = frozenset(),
    user_password: bytes | None = None,
    owner_password: bytes = b"owner",
) -> bytes:
    """``pages[i]`` are the text runs of page i+1; ``image_pages`` (1-based) also get a 1x1 image."""
    encrypt = user_password is not None
    o = u = key = b""
    if encrypt:
        o, u, key = _security(user_password or b"", owner_password)
    objects: dict[int, bytes] = {}
    n_pages = len(pages)
    page_ids = [4 + 3 * i for i in range(n_pages)]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n_pages)
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    streams: dict[int, bytes] = {}
    for i, runs in enumerate(pages):
        pid, cid, iid = page_ids[i], page_ids[i] + 1, page_ids[i] + 2
        content = b"".join(
            b"BT /F1 %.1f Tf %.3f %.3f %.3f rg %.1f %.1f Td (%s) Tj ET\n"
            % (r.size, *r.rgb, r.x, r.y, _escape(r.text))
            for r in runs
        )
        xobj = b""
        if i + 1 in image_pages:
            content += b"q 200 0 0 200 100 300 cm /Im1 Do Q\n"
            xobj = b" /XObject << /Im1 %d 0 R >>" % iid
            streams[iid] = b"\x80"
            objects[iid] = (
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray"
                b" /BitsPerComponent 8 /Length 1 >>"
            )
        streams[cid] = content
        objects[cid] = b"<< /Length %d >>" % len(content)
        objects[pid] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R"
            b" /Resources << /Font << /F1 3 0 R >>%s >> >>" % (cid, xobj)
        )
    enc_id = max(objects) + 1 if encrypt else None
    if enc_id is not None:
        objects[enc_id] = b"<< /Filter /Standard /V 1 /R 2 /O <%s> /U <%s> /P %d >>" % (
            o.hex().encode(),
            u.hex().encode(),
            P_FLAGS,
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        body = objects[num]
        out += b"%d 0 obj\n%s" % (num, body)
        if num in streams:
            data = streams[num]
            if encrypt:
                obj_key = hashlib.md5(
                    key + num.to_bytes(3, "little") + (0).to_bytes(2, "little"), usedforsecurity=False
                ).digest()[: min(len(key) + 5, 16)]
                data = _rc4(obj_key, data)
            out += b"\nstream\n" + data + b"\nendstream"
        out += b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num] if num in offsets else b"0000000000 65535 f \n"
    trailer = b"<< /Size %d /Root 1 0 R" % size
    if enc_id is not None:
        trailer += b" /Encrypt %d 0 R /ID [<%s> <%s>]" % (
            enc_id,
            FILE_ID.hex().encode(),
            FILE_ID.hex().encode(),
        )
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)
