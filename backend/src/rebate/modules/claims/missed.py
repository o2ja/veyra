# ruff: noqa: S608  # the SQL is assembled from module constants only; every value is a bound parameter
"""Missed-claims scan (BUSINESS_LOGIC.md §11, FR-CL-04).

A sales line is *missed* when the newest result of any completed run for it is ``eligible`` and it has no active
(``released_at IS NULL``) claim line. "Newest" is per line (latest completed run, then latest result id), so runs
with different scopes compose; superseded source-line versions (``is_current`` false) never count.
Exceptions are reported separately by code; ``outside_claim_window`` results with a positive stored amount are
"lost to expiry".

ponytail: ``DISTINCT ON`` over all of a tenant's results on every call; if it measures slow in S11, keep a
``latest_result`` projection maintained at run completion.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.orm import Session

LATEST = """
latest AS (
    SELECT DISTINCT ON (r.sales_document_line_id)
        r.id, r.tenant_id, r.run_id, r.sales_document_line_id, r.document_number, r.line_number, r.invoice_date,
        r.manufacturer_id, r.agreement_id, r.agreement_version_id, r.status, r.exception_code,
        r.eligible_quantity, r.claim_uom, r.claim_unit_amount, r.claim_amount, r.flags
    FROM calculation_results r
    JOIN calculation_runs cr ON cr.tenant_id = r.tenant_id AND cr.id = r.run_id AND cr.status = 'completed'
    JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id AND s.is_current
    WHERE r.tenant_id = :t
    ORDER BY r.sales_document_line_id, cr.completed_at DESC, r.id DESC
)
"""
UNCLAIMED = (
    "NOT EXISTS (SELECT 1 FROM claim_lines c WHERE c.tenant_id = l.tenant_id "
    "AND c.sales_document_line_id = l.sales_document_line_id AND c.released_at IS NULL)"
)
COLUMNS = (
    "l.id, l.run_id, l.sales_document_line_id, l.document_number, l.line_number, l.invoice_date, "
    "l.manufacturer_id, l.agreement_id, l.agreement_version_id, l.eligible_quantity, l.claim_uom, "
    "l.claim_unit_amount, l.claim_amount, l.flags"
)


def _filters(
    manufacturer_id: UUID | None,
    agreement_id: UUID | None,
    invoice_from: date | None,
    invoice_to: date | None,
) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    for clause, name, value in (
        ("l.manufacturer_id = :mfr", "mfr", manufacturer_id),
        ("l.agreement_id = :agr", "agr", agreement_id),
        ("l.invoice_date >= :d_from", "d_from", invoice_from),
        ("l.invoice_date <= :d_to", "d_to", invoice_to),
    ):
        if value is not None:
            where.append(clause)
            params[name] = value
    return where, params


def missed(
    db: Session,
    tenant_id: UUID,
    *,
    manufacturer_id: UUID | None = None,
    agreement_id: UUID | None = None,
    invoice_from: date | None = None,
    invoice_to: date | None = None,
    after: UUID | None = None,
    limit: int | None = None,
    result_ids: list[UUID] | None = None,
    stable_order: bool = False,
) -> list[Row[Any]]:
    """Eligible, unclaimed lines. Default order: result id descending (keyset ``after``). ``stable_order`` sorts by
    invoice date, document and line (claim-reference numbering). ``result_ids`` restricts to those results."""
    where, params = _filters(manufacturer_id, agreement_id, invoice_from, invoice_to)
    params["t"] = tenant_id
    if after is not None:
        where.append("l.id < :after")
        params["after"] = after
    if result_ids is not None:
        where.append("l.id = ANY(:ids)")
        params["ids"] = result_ids
    order = "l.invoice_date, l.document_number, l.line_number, l.id" if stable_order else "l.id DESC"
    sql = (
        f"WITH {LATEST} SELECT {COLUMNS} FROM latest l WHERE l.status = 'eligible' AND {UNCLAIMED} "  # nosec B608 (module constants, bound values)
        + "".join(f"AND {w} " for w in where)
        + f"ORDER BY {order}"
    )
    if limit is not None:
        sql += " LIMIT :n"
        params["n"] = limit
    return list(db.execute(text(sql), params))


def summary(
    db: Session,
    tenant_id: UUID,
    *,
    manufacturer_id: UUID | None = None,
    agreement_id: UUID | None = None,
    invoice_from: date | None = None,
    invoice_to: date | None = None,
) -> dict[str, Any]:
    where, params = _filters(manufacturer_id, agreement_id, invoice_from, invoice_to)
    params["t"] = tenant_id
    extra = "".join(f"AND {w} " for w in where)
    groups = db.execute(
        text(
            f"WITH {LATEST} SELECT l.manufacturer_id, m.code AS manufacturer_code, m.name AS manufacturer_name, "  # nosec B608 (module constants, bound values)
            "l.agreement_id, a.external_number AS agreement_number, count(*) AS lines, "
            "COALESCE(sum(l.claim_amount), 0) AS amount "
            "FROM latest l "
            "LEFT JOIN manufacturers m ON m.tenant_id = l.tenant_id AND m.id = l.manufacturer_id "
            "LEFT JOIN agreements a ON a.tenant_id = l.tenant_id AND a.id = l.agreement_id "
            f"WHERE l.status = 'eligible' AND {UNCLAIMED} {extra}"
            "GROUP BY l.manufacturer_id, m.code, m.name, l.agreement_id, a.external_number "
            "ORDER BY m.code, a.external_number"
        ),
        params,
    ).all()
    exceptions = db.execute(
        text(
            f"WITH {LATEST} SELECT l.exception_code AS code, count(*) AS lines, "  # nosec B608 (module constants, bound values)
            "COALESCE(sum(l.claim_amount), 0) AS stored_amount FROM latest l "
            f"WHERE l.status = 'exception' {extra}GROUP BY l.exception_code ORDER BY l.exception_code"
        ),
        params,
    ).all()
    lost = db.execute(
        text(
            f"WITH {LATEST} SELECT count(*) AS lines, COALESCE(sum(l.claim_amount), 0) AS amount FROM latest l "  # nosec B608 (module constants, bound values)
            "WHERE l.status = 'exception' AND l.exception_code = 'outside_claim_window' AND l.claim_amount > 0 "
            + extra
        ),
        params,
    ).one()
    return {
        "totals": {
            "lines": sum(g.lines for g in groups),
            "amount": sum((g.amount for g in groups), Decimal(0)),
        },
        "groups": [g._mapping for g in groups],
        "exceptions": [e._mapping for e in exceptions],
        "lost_to_expiry": {"lines": lost.lines, "amount": lost.amount},
    }
