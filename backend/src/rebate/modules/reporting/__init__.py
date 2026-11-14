"""reporting module: the monthly summary, dashboard, drill-down, trace resolver and audit pack (S10). It reads the
claims, reconciliation and recovery modules through their services. The tier tracker is S5's ``GET /rebate-tracker``."""

from sqlalchemy.orm import Session

from rebate.modules import reconciliation
from rebate.modules.masterdata import Who
from rebate.modules.reporting import jobs as _jobs
from rebate.modules.reporting.router import router


def _on_finalise(db: Session, who: Who, rem: reconciliation.FinalisedRemittance) -> None:
    _jobs.request_refresh(db, who.tenant_id)  # same transaction: the job exists iff the finalisation commits


reconciliation.on_finalise(_on_finalise)

__all__ = ["router"]
