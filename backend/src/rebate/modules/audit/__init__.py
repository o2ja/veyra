"""audit module: append-only audit log writer and search (FR-AU-01..03). Public API below."""

from rebate.modules.audit import jobs as _jobs  # noqa: F401  (registers the audit_seal job)
from rebate.modules.audit.events import Action
from rebate.modules.audit.jobs import schedule as schedule_seal
from rebate.modules.audit.router import router
from rebate.modules.audit.service import Actor, record

__all__ = ["Action", "Actor", "record", "router", "schedule_seal"]
