"""ingestion module: import batches, mapping templates, readers/normalisers, loader, source transaction
tables and their browse API (FR-IM-01..08). Public API below."""

from rebate.modules.ingestion import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.ingestion.router import router

__all__ = ["router"]
