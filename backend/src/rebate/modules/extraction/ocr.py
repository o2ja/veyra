"""S6.1 OCR execution (AI_PROCESSING.md §3.3): ``ocrmypdf``/Tesseract for the pages that need it, in a
subprocess with CPU, memory and wall-clock limits, a private temp dir per call and a scrubbed environment.

Only the pages the text extraction marked ``needs_ocr`` are OCRed; they replace the native page with
``text_method="ocr"`` (the verifier then lowers confidence to medium). A failed OCR leaves the page empty
(no invented text): the draft simply has nothing verified from that page.
"""

import dataclasses
import resource
import subprocess  # nosec B404  (fixed argument list, no shell)
import tempfile
from pathlib import Path

from rebate.modules.extraction.documents import (
    DEFAULT_LIMITS,
    ExtractionFailure,
    Limits,
    PdfText,
    extract_pdf,
)


class OcrFailed(RuntimeError):
    """The OCR subprocess failed, timed out or produced no readable PDF."""


def _limits(memory_bytes: int, cpu_seconds: int):  # type: ignore[no-untyped-def]
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))

    return apply


def run_ocr(
    pdf: bytes, pages: tuple[int, ...], *, timeout_seconds: int = 180, memory_mb: int = 4096
) -> bytes:
    """OCR ``pages`` (1-based) of ``pdf`` and return the resulting PDF bytes."""
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        src, dst = Path(tmp, "in.pdf"), Path(tmp, "out.pdf")
        src.write_bytes(pdf)
        cmd = [
            "ocrmypdf", "--quiet", "--force-ocr", "--jobs", "1", "--language", "eng",
            "--pages", ",".join(str(p) for p in pages), "--output-type", "pdf",
            str(src), str(dst),
        ]  # fmt: skip
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": tmp,
            "TMPDIR": tmp,
            "OMP_THREAD_LIMIT": "1",
            "LANG": "C.UTF-8",
        }
        try:
            done = subprocess.run(  # noqa: S603  # nosec B603  (constant program, our own temp paths)
                cmd,
                env=env,
                cwd=tmp,
                capture_output=True,
                timeout=timeout_seconds,
                preexec_fn=_limits(memory_mb * 1024 * 1024, timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrFailed("timeout") from exc
        if done.returncode != 0 or not dst.exists():
            raise OcrFailed(f"exit_{done.returncode}")
        return dst.read_bytes()


def apply_ocr(
    pdf: bytes,
    text: PdfText,
    limits: Limits = DEFAULT_LIMITS,
    *,
    timeout_seconds: int = 180,
    memory_mb: int = 4096,
) -> tuple[PdfText, str | None]:
    """Replace every ``needs_ocr`` page by its OCR text. Returns ``(document, failure_code | None)``."""
    pages = text.needs_ocr_pages
    if not pages:
        return text, None
    try:
        out = extract_pdf(run_ocr(pdf, pages, timeout_seconds=timeout_seconds, memory_mb=memory_mb), limits)
    except OcrFailed as exc:
        return text, f"ocr_failed:{exc}"
    if isinstance(out, ExtractionFailure) or len(out.pages) != len(text.pages):
        return text, "ocr_unreadable"
    ocr = {p.number: p for p in out.pages}
    merged = tuple(
        dataclasses.replace(ocr[p.number], needs_ocr=False, text_method="ocr") if p.number in pages else p
        for p in text.pages
    )
    return PdfText(merged), None
