"""extraction module (S6): document text extraction/OCR, the AI gateway, deterministic verification of AI
output, the ``extract_agreement`` job, extraction/usage/mapping APIs. Public API.
"""

from rebate.modules.extraction import jobs as _jobs  # noqa: F401  (registers job handlers)
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
from rebate.modules.extraction.router import (
    ExtractionResult,
    router,
)
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
    "ExtractionResult",
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
    "router",
    "run_verification",
]
