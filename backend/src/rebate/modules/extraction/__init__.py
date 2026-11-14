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
from rebate.modules.extraction.gateway import Gateway, GatewayError, make_provider
from rebate.modules.extraction.injection import Indicator, IndicatorKind, detect
from rebate.modules.extraction.models import DocumentExtraction
from rebate.modules.extraction.prompts import RULES as PROMPT_RULES
from rebate.modules.extraction.prompts import serialise as serialise_document
from rebate.modules.extraction.router import (
    ExtractionResult,
    router,
)
from rebate.modules.extraction.service import MAX_PROMPT_CHARS, AiNotEnabled, ai_engine, audit_invocations
from rebate.modules.extraction.service import _write as write_result
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
    "MAX_PROMPT_CHARS",
    "PROMPT_RULES",
    "AiNotEnabled",
    "CandidateField",
    "CandidateFormatError",
    "Cell",
    "Confidence",
    "DocumentExtraction",
    "Evidence",
    "ExtractionFailure",
    "ExtractionResult",
    "FailureReason",
    "Gateway",
    "GatewayError",
    "Indicator",
    "IndicatorKind",
    "Limits",
    "MergedField",
    "Page",
    "PdfText",
    "SheetText",
    "VerificationReport",
    "ai_engine",
    "audit_invocations",
    "detect",
    "extract_pdf",
    "extract_xlsx",
    "flatten",
    "make_provider",
    "router",
    "run_verification",
    "serialise_document",
    "write_result",
]
