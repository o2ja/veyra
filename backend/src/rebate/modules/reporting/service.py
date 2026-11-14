"""Dashboard summary, drill-down and trace resolver (FR-RP-01, W14, API_SPECIFICATION.md §2.9).

The summary table is rebuilt for a tenant in one transaction from the three modules' figure definitions (``core.figures``);
the drill-down rows and totals come from the same definitions, so every figure drills to rows that sum to it. Money is
only ever added here (Decimal), never computed."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import NotFound
from rebate.core.figures import FigureSet, Filter
from rebate.core.ids import new_id
from rebate.core.trace import merge
from rebate.modules import claims, reconciliation, recovery, tenancy
from rebate.modules.reporting.models import SummaryClaimsMonthly as S

# metric -> the module that owns its definition (ARCHITECTURE.md §3: reporting reads claims, reconciliation, recovery)
SOURCES: dict[str, FigureSet] = {
    "claimed": claims.figures,
    "collected": reconciliation.figures,
    "short_paid": recovery.figures,
    "recovered": recovery.figures,
    "written_off": recovery.figures,
    "at_risk": recovery.figures,
    "expiring": recovery.figures,
    "recovered_after_write_off": recovery.figures,
}
METRICS = tuple(SOURCES)
FOLLOW_LIMIT = 50  # claim lines followed from one remittance line in a trace


def _tz_and_today(db: Session, tenant: UUID) -> tuple[str, date]:
    tz = tenancy.get_settings(db, tenant).business_timezone
    return tz, clock.now().astimezone(ZoneInfo(tz)).date()


def refresh(db: Session, tenant: UUID) -> int:
    """Rebuild the tenant's summary rows (idempotent: same data, same rows); returns the row count."""
    tz, today = _tz_and_today(db, tenant)
    cells: dict[tuple[UUID, UUID | None, date], dict[str, Decimal]] = {}
    for metric, source in SOURCES.items():
        for r in source.by_month(db, tenant, metric, tz, today):
            cells.setdefault((r.manufacturer_id, r.branch_id, r.month), {})[metric] = r.amount
    now = clock.now()
    db.execute(delete(S).where(S.tenant_id == tenant))
    if cells:
        db.execute(
            insert(S),
            [
                {
                    "tenant_id": tenant,
                    "id": new_id(),
                    "manufacturer_id": m,
                    "branch_id": b,
                    "month": month,
                    **values,
                    "as_of_date": today,
                    "refreshed_at": now,
                }
                for (m, b, month), values in sorted(
                    cells.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2])
                )
            ],
        )
    return len(cells)


def _where(tenant: UUID, flt: Filter) -> list[Any]:
    clauses: list[Any] = [S.tenant_id == tenant]
    if flt.manufacturer_id is not None:
        clauses.append(S.manufacturer_id == flt.manufacturer_id)
    if flt.branch_id is not None:
        clauses.append(S.branch_id == flt.branch_id)
    if flt.month is not None:
        clauses.append(S.month == flt.month)
    return clauses


def dashboard(db: Session, tenant: UUID, flt: Filter) -> dict[str, Any]:
    """Σ of the summary rows matching the filter, per figure and per month, with the refresh instant."""
    cols = [func.coalesce(func.sum(getattr(S, m)), 0) for m in METRICS]
    where = _where(tenant, flt)
    totals = db.execute(select(*cols).where(*where)).one()
    by_month = db.execute(select(S.month, *cols).where(*where).group_by(S.month).order_by(S.month)).all()
    stamp = db.execute(
        select(func.max(S.refreshed_at), func.max(S.as_of_date)).where(S.tenant_id == tenant)
    ).one()
    return {
        "as_of": stamp[0],
        "as_of_date": stamp[1],
        "figures": dict(zip(METRICS, totals, strict=True)),
        "months": [{"month": r[0], "figures": dict(zip(METRICS, r[1:], strict=True))} for r in by_month],
    }


def drilldown(
    db: Session, tenant: UUID, metric: str, flt: Filter, after: UUID | None, limit: int
) -> dict[str, Any]:
    """The rows behind one figure (``limit + 1`` rows: the caller pages), the figure in the summary, and the total
    of all matching rows now. ``consistent`` is false only if the data changed since the last refresh."""
    tz, today = _tz_and_today(db, tenant)
    summary = dashboard(db, tenant, flt)
    as_of_date = summary["as_of_date"] or today  # the date the summary's at-risk figures were computed for
    source = SOURCES[metric]
    total = source.total(db, tenant, metric, tz, as_of_date, flt)
    return {
        "metric": metric,
        "as_of": summary["as_of"],
        "figure": summary["figures"][metric],
        "total": total,
        "consistent": total == summary["figures"][metric],
        "rows": source.rows(db, tenant, metric, tz, as_of_date, flt, after, limit + 1),
    }


# ---- trace ---------------------------------------------------------------------------------------------------


def _claim_chain(db: Session, tenant: UUID, claim_line_id: UUID) -> list[dict[str, Any]] | None:
    chain = claims.trace_claim_line(db, tenant, claim_line_id)
    if chain is None:
        return None
    return merge(
        chain,
        reconciliation.trace_payments(db, tenant, claim_line_id),
        recovery.case_nodes(db, tenant, claim_line_id=claim_line_id),
    )


def _remittance_chain(db: Session, tenant: UUID, remittance_line_id: UUID) -> list[dict[str, Any]] | None:
    found = reconciliation.trace_remittance_line(db, tenant, remittance_line_id)
    if found is None:
        return None
    nodes, claim_line_ids = found
    followed = [claims.trace_claim_line(db, tenant, c) or [] for c in claim_line_ids[:FOLLOW_LIMIT]]
    return merge(nodes, *followed, recovery.case_nodes(db, tenant, remittance_line_id=remittance_line_id))


def trace(db: Session, tenant: UUID, kind: str, ident: UUID) -> list[dict[str, Any]]:
    """The chain from a record back to the source file row (and forward to payment and recovery). Unknown id or
    another tenant's id: ``NotFound``."""
    chain: list[dict[str, Any]] | None
    if kind == "claim_line":
        chain = _claim_chain(db, tenant, ident)
    elif kind == "remittance_line":
        chain = _remittance_chain(db, tenant, ident)
    elif kind == "claim_adjustment":
        adj = claims.trace_claim_adjustment(db, tenant, ident)
        if adj is None:
            raise NotFound()
        nodes, claim_line_id = adj
        chain = merge(nodes, _claim_chain(db, tenant, claim_line_id) or []) if claim_line_id else nodes
    elif kind == "calculation_result":
        chain = claims.trace_calculation_result(db, tenant, ident)
    elif kind == "variance_case":
        found = recovery.trace_case(db, tenant, ident)
        if found is None:
            raise NotFound()
        nodes, claim_line_id, remittance_line_id = found
        parts = [nodes]
        if claim_line_id is not None:
            parts.append(_claim_chain(db, tenant, claim_line_id) or [])
        if remittance_line_id is not None:
            parts.append(_remittance_chain(db, tenant, remittance_line_id) or [])
        chain = merge(*parts)
    else:  # unreachable: the route restricts ``kind`` to these five
        raise NotFound()
    if chain is None:
        raise NotFound()
    return chain
