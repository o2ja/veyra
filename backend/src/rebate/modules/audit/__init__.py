"""audit module: append-only audit log writer and search (FR-AU-01..03). Public API below."""

from rebate.modules.audit.events import Action
from rebate.modules.audit.router import router
from rebate.modules.audit.service import Actor, record

__all__ = ["Action", "Actor", "record", "router"]
