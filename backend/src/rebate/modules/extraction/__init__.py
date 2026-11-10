"""extraction module (S6): document text extraction and deterministic verification of AI output.

Pure parts so far (S6.1 without OCR execution, S6.3): no models, router or provider calls. Public API.
"""

from rebate.modules.extraction.documents import (
    Cell,
    ExtractionFailure,
    FailureReason,
    Limits,
    Page,
    PdfText,
    SheetText,
    extract_pdf,
    extract_xlsx,
)
from rebate.modules.extraction.injection import Indicator, IndicatorKind, detect
from rebate.modules.extraction.verification import (
    CandidateField,
    CandidateFormatError,
    Confidence,
    Evidence,
    MergedField,
    VerificationReport,
    flatten,
    run_verification,
)

__all__ = [
    "CandidateField",
    "CandidateFormatError",
    "Cell",
    "Confidence",
    "Evidence",
    "ExtractionFailure",
    "FailureReason",
    "Indicator",
    "IndicatorKind",
    "Limits",
    "MergedField",
    "Page",
    "PdfText",
    "SheetText",
    "VerificationReport",
    "detect",
    "extract_pdf",
    "extract_xlsx",
    "flatten",
    "run_verification",
]
