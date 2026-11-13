"""reconciliation module: remittance ingestion (CSV/XLSX mapping, recorded-fixture PDF extraction), matching
persistence, human decisions and variance findings (S8). Public API below; the finalisation hook is for S9."""

from rebate.modules.reconciliation import ingest as _ingest  # noqa: F401  (registers job handlers)
from rebate.modules.reconciliation import matching as _matching  # noqa: F401
from rebate.modules.reconciliation.matching import FinalisedRemittance, Finding, on_finalise
from rebate.modules.reconciliation.reporting_reads import (
    confirmed_payments,
    figures,
    trace_payments,
    trace_remittance_line,
)
from rebate.modules.reconciliation.router import router

__all__ = [
    "FinalisedRemittance",
    "Finding",
    "confirmed_payments",
    "figures",
    "on_finalise",
    "router",
    "trace_payments",
    "trace_remittance_line",
]
