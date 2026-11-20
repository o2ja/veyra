"""recovery module: variance cases, deadlines, notifications (S9). Public API."""

from sqlalchemy.orm import Session

from rebate.modules import reconciliation
from rebate.modules.masterdata import Who
from rebate.modules.recovery import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.recovery.reporting_reads import AT_RISK_DAYS, case_nodes, figures, trace_case
from rebate.modules.recovery.router import router
from rebate.modules.recovery.service import business_today, deadline_figures, open_cases


def _on_finalise(db: Session, who: Who, rem: reconciliation.FinalisedRemittance) -> None:
    open_cases(db, who, rem)  # inside the finalisation transaction: a failure rolls the finalisation back


reconciliation.on_finalise(_on_finalise)

__all__ = [
    "AT_RISK_DAYS",
    "business_today",
    "case_nodes",
    "deadline_figures",
    "figures",
    "open_cases",
    "router",
    "trace_case",
]
