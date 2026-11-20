"""agreements module: one draft pipeline (manual, spreadsheet template, AI later), AgreementValidator,
four-eyes approval, immutable versions with split-supersede, cancellation, expiry sweep, version history
and diff (FR-AG-01..12). Public API below."""

from rebate.modules.agreements import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.agreements.calc_reads import (
    VersionsInForce,
    draft_for_preview,
    version_rules,
    versions_in_force,
)
from rebate.modules.agreements.models import AgreementDraft
from rebate.modules.agreements.router import router
from rebate.modules.agreements.schemas import Payload
from rebate.modules.agreements.service import (
    business_today,
    content_hash,
    create_ai_draft,
    draft_counts,
    version_payload,
)
from rebate.modules.agreements.validator import validate

__all__ = [
    "AgreementDraft",
    "Payload",
    "VersionsInForce",
    "business_today",
    "content_hash",
    "create_ai_draft",
    "draft_counts",
    "draft_for_preview",
    "router",
    "validate",
    "version_payload",
    "version_rules",
    "versions_in_force",
]
