# ruff: noqa: S608  # the SQL is assembled from module constants only; every value is a bound parameter
"""Read-only recovery data for the dashboard and trace resolver (S10; FR-RP-01, W14). The ``reporting`` module calls
these through ``rebate.modules.recovery``, never the tables.

Figures, all by the month the case was opened (business time zone), branch = the invoice's branch of the claim line:

* ``short_paid``: variance of cases of money we claimed and were not paid (``short_paid``, ``rejected``, ``unpaid``);
  ``overpaid``, ``unmatched_payment`` and ``source_changed`` are not money owed to us.
* ``recovered`` / ``written_off``: the cases' ``recovered_amount`` / ``written_off_amount``.
* ``at_risk``: open variance (variance minus recovered) of live reclaimable cases whose deadline is unknown or at
  most ``AT_RISK_DAYS`` days after the as-of date (overdue included), the definition of ``GET /cases/summary``
  restricted to reclaimable types.
* ``expiring``: the part of ``at_risk`` with a known deadline from the as-of date up to ``AT_RISK_DAYS`` days."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.core.figures import FigureSet
from rebate.core.trace import node

AT_RISK_DAYS = 14
RECLAIMABLE = "('short_paid', 'rejected', 'unpaid')"
LIVE = "('open', 'to_reclaim', 'submitted')"
_OPEN = f"v.status IN {LIVE} AND v.case_type IN {RECLAIMABLE}"


def _source(amount: str, where: str) -> str:
    return (
        "SELECT v.id, 'variance_case' AS trace_kind, v.id AS trace_id, v.manufacturer_id, sd.branch_id, "
        f"date_trunc('month', v.created_at AT TIME ZONE :tz)::date AS month, {amount} AS amount, "
        "v.case_type || ' ' || v.reason_code AS label "
        "FROM variance_cases v "
        "LEFT JOIN claim_lines c ON c.tenant_id = v.tenant_id AND c.id = v.claim_line_id "
        "LEFT JOIN sales_document_lines s ON s.tenant_id = c.tenant_id AND s.id = c.sales_document_line_id "
        "LEFT JOIN sales_documents sd ON sd.tenant_id = s.tenant_id AND sd.id = s.document_id "
        f"WHERE v.tenant_id = :t AND {where}"
    )


_REMAINING = "v.variance_amount - v.recovered_amount"
figures = FigureSet(
    {
        "short_paid": _source("v.variance_amount", f"v.case_type IN {RECLAIMABLE}"),
        "recovered": _source("v.recovered_amount", "v.recovered_amount > 0"),
        "written_off": _source("v.written_off_amount", "v.written_off_amount > 0"),
        "at_risk": _source(
            _REMAINING,
            f"{_OPEN} AND (v.deadline_date IS NULL OR v.deadline_date <= CAST(:as_of AS date) + {AT_RISK_DAYS})",
        ),
        "expiring": _source(
            _REMAINING,
            f"{_OPEN} AND v.deadline_date BETWEEN CAST(:as_of AS date) AND CAST(:as_of AS date) + {AT_RISK_DAYS}",
        ),
    }
)


def _case_node(r: Any) -> dict[str, Any]:
    return node(
        "variance_case",
        r.id,
        f"{r.case_type} {r.reason_code}",
        status=r.status,
        variance_amount=r.variance_amount,
        recovered_amount=r.recovered_amount,
        written_off_amount=r.written_off_amount,
        deadline_date=r.deadline_date,
        claimed_amount=r.claimed_amount,
        paid_amount=r.paid_amount,
    )


_COLS = (
    "SELECT id, case_type, reason_code, status, variance_amount, recovered_amount, written_off_amount, "
    "deadline_date, claimed_amount, paid_amount, claim_line_id, remittance_line_id FROM variance_cases "
)


def trace_case(
    db: Session, tenant: UUID, case_id: UUID
) -> tuple[list[dict[str, Any]], UUID | None, UUID | None] | None:
    """The case node plus the claim line and remittance line it points to (to follow on). ``None`` if unknown."""
    r = db.execute(text(f"{_COLS}WHERE tenant_id = :t AND id = :id"), {"t": tenant, "id": case_id}).first()
    return None if r is None else ([_case_node(r)], r.claim_line_id, r.remittance_line_id)


def case_nodes(
    db: Session, tenant: UUID, *, claim_line_id: UUID | None = None, remittance_line_id: UUID | None = None
) -> list[dict[str, Any]]:
    """The cases opened for a claim line or a remittance line."""
    column = "claim_line_id" if claim_line_id is not None else "remittance_line_id"
    value = claim_line_id if claim_line_id is not None else remittance_line_id
    return [
        _case_node(r)
        for r in db.execute(
            text(f"{_COLS}WHERE tenant_id = :t AND {column} = :v ORDER BY id"), {"t": tenant, "v": value}
        )
    ]
