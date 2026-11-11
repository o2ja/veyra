"""agreements module: one draft pipeline (manual, spreadsheet template, AI later), AgreementValidator,
four-eyes approval, immutable versions with split-supersede, cancellation, expiry sweep, version history
and diff (FR-AG-01..12). Public API below."""

from rebate.modules.agreements import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.agreements.router import router
from rebate.modules.agreements.schemas import Payload
from rebate.modules.agreements.service import business_today, content_hash, version_payload
from rebate.modules.agreements.validator import validate

__all__ = ["Payload", "business_today", "content_hash", "router", "validate", "version_payload"]
