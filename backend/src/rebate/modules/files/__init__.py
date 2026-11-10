"""files module: uploads, quarantine, malware scan, presigned downloads (FR-FI-01..04). Public API."""

from rebate.modules.files import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.files.router import router

__all__ = ["router"]
