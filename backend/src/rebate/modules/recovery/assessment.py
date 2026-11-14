"""Dispute assessment of a case from the frozen calculation (BUSINESS_LOGIC.md §10.4).

The facts come from the claim line's stored trace and the claim batch; the judgement is
``rebate_matching.dispute.assess_dispute`` (pure, deterministic; no LLM). A fact the trace does not hold stays
``None``, which the matching code turns into ``unknown`` (never guessed).
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate_matching.dispute import DisputeFacts, assess_dispute


def _step(trace: dict[str, Any], name: str, version_id: str | None = None) -> dict[str, Any] | None:
    for s in trace.get("steps", []):
        if s.get("step") == name and (version_id is None or s.get("version_id") in (None, version_id)):
            return s  # type: ignore[no-any-return]
    return None


def _dec(v: Any) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


def _date(v: Any) -> date | None:
    return None if v in (None, "") else date.fromisoformat(str(v))


def facts_from_trace(
    trace: dict[str, Any], version_id: UUID, submitted_on: date | None, other_active_claims: int | None
) -> DisputeFacts:
    v = str(version_id)
    dc = _step(trace, "date_check", v)
    scope = _step(trace, "customer_scope")
    price = _step(trace, "price_check")
    cap = _step(trace, "cap")
    window = _step(trace, "claim_window")
    rng = (dc or {}).get("range") or [None, None]
    price_checked = price is not None and not price.get("skipped")
    consumption = None
    if cap is not None:
        consumption = _dec(cap["consumed_before"]) + _dec(cap["eligible_qty"])  # type: ignore[operator]
    return DisputeFacts(
        line_date=_date((dc or {}).get("value")),
        valid_from=_date(rng[0]),
        valid_to=_date(rng[1]),
        customer_in_scope=None if scope is None else v in scope.get("remaining", []),
        unit_price=_dec(price["unit_price"]) if price_checked and price else None,
        sell_price=_dec(price["sell_price"]) if price_checked and price else None,
        price_tolerance=_dec(price["tolerance"]) or Decimal(0) if price_checked and price else Decimal(0),
        consumption=consumption,
        max_quantity=_dec(cap["max_quantity"]) if cap else None,
        eligible_quantity=_dec(cap["eligible_qty"]) if cap else None,
        other_active_claims=other_active_claims,
        submitted_on=submitted_on,
        claim_window_end=_date(window.get("deadline")) if window and not window.get("skipped") else None,
    )


def assess_case(
    db: Session,
    tenant_id: UUID,
    claim_line_id: UUID | None,
    remittance_line_id: UUID | None,
    reason_code: str,
) -> dict[str, Any] | None:
    """The stored ``dispute_assessment`` document, or ``None`` for a case without a claim line (nothing frozen to
    re-check). Every check lists its inputs, so the document is the explanation."""
    if claim_line_id is None:
        return None
    row = db.execute(
        text(
            "SELECT c.agreement_version_id, c.sales_document_line_id, c.id AS claim_line_id, "
            "r.trace, b.submitted_at FROM claim_lines c "
            "JOIN claim_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id "
            "LEFT JOIN calculation_results r ON r.tenant_id = c.tenant_id AND r.id = c.calculation_result_id "
            "WHERE c.tenant_id = :t AND c.id = :c"
        ),
        {"t": tenant_id, "c": claim_line_id},
    ).one()
    others: int | None = None
    if row.sales_document_line_id is not None:
        others = db.scalar(
            text(
                "SELECT (SELECT count(*) FROM claim_lines o WHERE o.tenant_id = :t "
                "AND o.sales_document_line_id = :s AND o.id <> :c AND o.released_at IS NULL) "
                "+ (SELECT count(*) FROM reconciliation_matches m WHERE m.tenant_id = :t "
                "AND m.claim_line_id = :c AND m.status = 'confirmed' "
                "AND m.remittance_line_id IS DISTINCT FROM CAST(:rl AS uuid))"
            ),
            {"t": tenant_id, "s": row.sales_document_line_id, "c": claim_line_id, "rl": remittance_line_id},
        )
    facts = facts_from_trace(row.trace or {}, row.agreement_version_id, row.submitted_at, others)
    result = assess_dispute(reason_code, facts)
    return {
        "assessment": result.assessment,
        "reason_code": reason_code,
        "claim_line_id": str(claim_line_id),
        "checks": [{"name": c.name, "inputs": c.inputs, "passed": c.passed} for c in result.checks],
    }
