"""Claim adjustments (S7.4; BUSINESS_LOGIC.md §5.3, §7, §9.3; DATABASE_SCHEMA.md §3.8).

Corrections to frozen claims are never edits: a completed run *proposes* signed adjustments, an approver approves or
rejects them, and an approved one is included in the next claim batch of its manufacturer (the batch total then
carries it). Three kinds come from a run:

* ``return_reversal``: a return line whose original is claimed (result status ``adjustment``, §7);
* ``agreement_change``: the run's result for a claimed line differs from the frozen amount, or is now an exception
  (§9.3) - also the proposal a source change produces (the new line version is compared with the claim of the old);
* ``rebate_clawback``: a negative accrual delta (§5.3, example R2).

Nothing here decides an amount: amounts are the engine's stored results and ledger deltas.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, FourEyesViolation, InvalidState, NotFound, ValidationFailed
from rebate.core.ids import new_id
from rebate.modules import audit, masterdata, tenancy
from rebate.modules.claims import frozen, service
from rebate.modules.claims.models import ClaimAdjustment
from rebate.modules.masterdata import Who
from rebate_engine.models import LineResult, Status
from rebate_engine.spa import CANNOT_BE_NET_CLAIMED, propose_recalculation


class AdjustmentAlreadyIncluded(Conflict):
    code = "adjustment_already_included"
    message = "Some of these adjustments are already in a claim batch."


class AuditedFourEyesViolation(FourEyesViolation):
    commit = True  # the audit entry of the refused approval must survive the error response


def view(a: ClaimAdjustment) -> dict[str, Any]:
    return {
        "manufacturer_id": a.manufacturer_id,
        "adjustment_type": a.adjustment_type,
        "claim_line_id": a.claim_line_id,
        "rebate_accrual_entry_id": a.rebate_accrual_entry_id,
        "calculation_result_id": a.calculation_result_id,
        "run_id": a.run_id,
        "amount": str(a.amount),
        "reason": a.reason,
        "status": a.status,
        "included_in_batch_id": a.included_in_batch_id,
    }


def _audit(db: Session, who: Who, action: audit.Action, a: ClaimAdjustment, **kw: Any) -> None:
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=action,
        entity_type="claim_adjustment",
        entity_id=a.id,
        meta=who.meta,
        **kw,
    )


# ---- reads --------------------------------------------------------------------------------------------------


def get(db: Session, tenant_id: UUID, adjustment_id: UUID, *, lock: bool = False) -> ClaimAdjustment:
    stmt = select(ClaimAdjustment).where(
        ClaimAdjustment.tenant_id == tenant_id, ClaimAdjustment.id == adjustment_id
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def list_adjustments(
    db: Session,
    tenant_id: UUID,
    *,
    status: str | None,
    manufacturer_id: UUID | None,
    adjustment_type: str | None,
    claim_line_id: UUID | None,
    batch_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> list[Row[Any]]:
    """Newest first, with the claim reference of the line each adjustment corrects."""
    after = masterdata.decode_cursor(cursor)
    return list(
        db.execute(
            text(
                "SELECT a.id, a.manufacturer_id, a.adjustment_type, a.claim_line_id, cl.claim_reference, "
                "a.rebate_accrual_entry_id, a.calculation_result_id, a.run_id, a.amount, a.reason, a.status, "
                "a.included_in_batch_id, a.proposed_by, a.approved_by, a.approved_at, a.created_at "
                "FROM claim_adjustments a "
                "LEFT JOIN claim_lines cl ON cl.tenant_id = a.tenant_id AND cl.id = a.claim_line_id "
                "WHERE a.tenant_id = :t "
                "AND (CAST(:status AS text) IS NULL OR a.status = :status) "
                "AND (CAST(:mfr AS uuid) IS NULL OR a.manufacturer_id = :mfr) "
                "AND (CAST(:kind AS text) IS NULL OR a.adjustment_type = :kind) "
                "AND (CAST(:line AS uuid) IS NULL OR a.claim_line_id = :line) "
                "AND (CAST(:batch AS uuid) IS NULL OR a.included_in_batch_id = :batch) "
                "AND (CAST(:after AS uuid) IS NULL OR a.id < :after) "
                "ORDER BY a.id DESC LIMIT :n"
            ),
            {
                "t": tenant_id,
                "status": status,
                "mfr": manufacturer_id,
                "kind": adjustment_type,
                "line": claim_line_id,
                "batch": batch_id,
                "after": after,
                "n": limit + 1,
            },
        )
    )


# ---- approve / reject ---------------------------------------------------------------------------------------


def approve(db: Session, who: Who, adjustment_id: UUID) -> ClaimAdjustment:
    a = get(db, who.tenant_id, adjustment_id, lock=True)
    if a.status != "proposed":
        raise InvalidState("Only a proposed adjustment can be approved.")
    if (
        tenancy.get_settings(db, who.tenant_id).four_eyes_required
        and a.proposed_by is not None
        and a.proposed_by == who.user_id
    ):
        _audit(db, who, audit.Action.CLAIM_ADJUSTMENT_FOUR_EYES_VIOLATION, a, after=view(a))
        raise AuditedFourEyesViolation()
    before = view(a)
    a.status, a.approved_by, a.approved_at = "approved", who.user_id, clock.now()
    db.flush()
    _audit(db, who, audit.Action.CLAIM_ADJUSTMENT_APPROVED, a, before=before, after=view(a))
    return a


def reject(db: Session, who: Who, adjustment_id: UUID) -> ClaimAdjustment:
    a = get(db, who.tenant_id, adjustment_id, lock=True)
    if a.status not in ("proposed", "approved"):
        raise InvalidState("Only a proposed or approved adjustment can be rejected.")
    before = view(a)
    a.status = "rejected"
    db.flush()
    _audit(db, who, audit.Action.CLAIM_ADJUSTMENT_REJECTED, a, before=before, after=view(a))
    return a


# ---- inclusion in a batch -----------------------------------------------------------------------------------


def lock_for_inclusion(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, ids: list[UUID]
) -> list[ClaimAdjustment]:
    """The approved adjustments ``ids`` of ``manufacturer_id``, row-locked. 422 for unknown, other-manufacturer or
    not-yet-approved ones; 409 ``adjustment_already_included`` for those already in a (live) batch."""
    ids = sorted(set(ids))
    rows = {
        a.id: a
        for a in db.scalars(
            select(ClaimAdjustment)
            .where(ClaimAdjustment.tenant_id == tenant_id, ClaimAdjustment.id.in_(ids))
            .order_by(ClaimAdjustment.id)
            .with_for_update()
        )
    }
    problems: list[dict[str, str]] = []
    for i in ids:
        a = rows.get(i)
        if a is None:
            problems.append(_problem("not_found", "Unknown adjustment.", i))
        elif a.manufacturer_id != manufacturer_id:
            problems.append(
                _problem("wrong_manufacturer", "The adjustment belongs to another manufacturer.", i)
            )
        elif a.status not in ("approved", "included"):
            problems.append(_problem("not_approved", "Only approved adjustments can be included.", i))
    if problems:
        raise ValidationFailed(details=problems)
    taken = [a for a in rows.values() if a.status == "included"]
    if taken:
        raise AdjustmentAlreadyIncluded(
            details=[
                {
                    **_problem("already_included", "The adjustment is already in a claim batch.", a.id),
                    "batch_id": str(a.included_in_batch_id),
                }
                for a in taken
            ]
        )
    return [rows[i] for i in ids]


def _problem(code: str, message: str, adjustment_id: UUID) -> dict[str, str]:
    return {
        "field": "include_adjustment_ids",
        "code": code,
        "message": message,
        "adjustment_id": str(adjustment_id),
    }


def include(db: Session, who: Who, rows: list[ClaimAdjustment], batch_id: UUID) -> None:
    """Attach locked ``rows`` to the draft batch ``batch_id`` (the database enforces draft + same manufacturer)."""
    for a in rows:
        before = view(a)
        a.status, a.included_in_batch_id = "included", batch_id
        db.flush()
        _audit(db, who, audit.Action.CLAIM_ADJUSTMENT_INCLUDED, a, before=before, after=view(a))


def release(db: Session, who: Who, batch_id: UUID) -> int:
    """Voiding a batch gives its adjustments back: ``included`` -> ``approved`` and the batch link is dropped."""
    rows = list(
        db.scalars(
            select(ClaimAdjustment)
            .where(
                ClaimAdjustment.tenant_id == who.tenant_id,
                ClaimAdjustment.included_in_batch_id == batch_id,
            )
            .order_by(ClaimAdjustment.id)
            .with_for_update()
        )
    )
    for a in rows:
        before = view(a)
        a.status, a.included_in_batch_id = "approved", None
        db.flush()
        _audit(db, who, audit.Action.CLAIM_ADJUSTMENT_RELEASED, a, before=before, after=view(a))
    return len(rows)


def total(rows: list[ClaimAdjustment]) -> Decimal:
    return sum((a.amount for a in rows), Decimal(0))


# ---- proposals ----------------------------------------------------------------------------------------------


def _live_agreement_changes(db: Session, tenant_id: UUID, claim_line_ids: list[UUID]) -> dict[UUID, Decimal]:
    """Per claim line: what earlier non-rejected ``agreement_change`` adjustments already correct."""
    return {
        r.claim_line_id: r.total
        for r in db.execute(
            text(
                "SELECT claim_line_id, sum(amount) AS total FROM claim_adjustments WHERE tenant_id = :t "
                "AND adjustment_type = 'agreement_change' AND status <> 'rejected' "
                "AND claim_line_id = ANY(CAST(:ids AS uuid[])) GROUP BY claim_line_id"
            ),
            {"t": tenant_id, "ids": claim_line_ids},
        )
    }


def _insert(db: Session, who: Who, run_id: UUID, **values: Any) -> ClaimAdjustment | None:
    return db.execute(
        insert(ClaimAdjustment)
        .values(
            tenant_id=who.tenant_id,
            id=new_id(),
            run_id=run_id,
            status="proposed",
            proposed_by=who.user_id,
            created_at=clock.now(),
            **values,
        )
        .on_conflict_do_nothing()
        .returning(ClaimAdjustment)
    ).scalar_one_or_none()


def propose(
    db: Session, who: Who, run_id: UUID, source: frozen.FrozenClaimSource | None = None
) -> tuple[int, list[ClaimAdjustment]]:
    """Compare the completed run ``run_id`` with the frozen claims and the ledger; insert the missing proposals.
    Idempotent: a second call (or a later run saying the same thing) proposes nothing new. Returns the number of
    things compared and the adjustments created."""
    source = source or frozen.source
    t = who.tenant_id
    run = service.completed_run(db, t, run_id)
    # one writer at a time per tenant, shared with batch creation (inclusion): proposals are netted against the
    # corrections that exist, so two concurrent proposals would otherwise both see "nothing yet"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"claims:{t}"})
    superseded = db.scalar(
        text(
            "SELECT count(*) FROM calculation_results r "
            "JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id "
            "WHERE r.tenant_id = :t AND r.run_id = :r "
            "AND r.status IN ('eligible', 'exception', 'not_applicable', 'adjustment') "
            "AND (NOT s.is_current OR EXISTS (SELECT 1 FROM calculation_results r2 "
            "JOIN calculation_runs cr2 ON cr2.tenant_id = r2.tenant_id AND cr2.id = r2.run_id "
            "AND cr2.status = 'completed' WHERE r2.tenant_id = r.tenant_id "
            "AND r2.sales_document_line_id = r.sales_document_line_id AND r2.run_id <> r.run_id "
            "AND (cr2.completed_at > :done OR (cr2.completed_at = :done AND cr2.id > :r))))"
        ),
        {"t": t, "r": run_id, "done": run.completed_at},
    )
    if superseded:
        raise InvalidState(
            f"{superseded} line(s) of this run have a newer calculation; propose adjustments from the latest run."
        )
    rows = db.execute(
        text(
            "SELECT r.id, r.sales_document_line_id, r.document_number, r.line_number, r.status, r.exception_code, "
            "r.claim_amount, r.flags, s.original_line_id FROM calculation_results r "
            "JOIN sales_document_lines s ON s.tenant_id = r.tenant_id AND s.id = r.sales_document_line_id "
            "WHERE r.tenant_id = :t AND r.run_id = :r "
            "AND r.status IN ('eligible', 'exception', 'not_applicable', 'adjustment') ORDER BY r.id"
        ),
        {"t": t, "r": run_id},
    ).all()
    made: list[ClaimAdjustment] = []
    compared = 0

    # agreement_change (§9.3): the run's result for a claimed line against the frozen claim
    by_line = {r.sales_document_line_id: r for r in rows if r.status != "adjustment"}
    claims = source.frozen_claims(db, t, sorted(by_line))
    compared += len(claims)
    already = _live_agreement_changes(db, t, [c.claim_line_id for c in claims.values()])
    for line_id, claim in sorted(claims.items()):
        row = by_line[line_id]
        new = LineResult(
            line_id=str(line_id),
            status=Status(row.status),
            exception_code=None,
            agreement_version_id=None,
            claim_amount=row.claim_amount,
        )
        target = propose_recalculation(claim.claim_amount, new) or Decimal(0)
        # a correction already proposed, approved or included (from an earlier run) is not proposed twice
        amount = target - already.get(claim.claim_line_id, Decimal(0))
        if amount == 0:
            continue
        done = already.get(claim.claim_line_id, Decimal(0))
        what = (
            f"now {row.exception_code or row.status}"
            if row.status != "eligible"
            else f"new amount {row.claim_amount}"
        )
        reason = (
            f"Recalculation {run.id}: {what}, frozen claim {claim.claim_amount}; correction due {target}, "
            f"already corrected {done}, proposed {amount}"
        )
        a = _insert(
            db,
            who,
            run_id,
            manufacturer_id=claim.manufacturer_id,
            adjustment_type="agreement_change",
            claim_line_id=claim.claim_line_id,
            calculation_result_id=row.id,
            amount=amount,
            reason=reason,
        )
        if a is not None:
            made.append(a)

    # return_reversal (§7): a return line after the original was claimed
    returns = [r for r in rows if r.status == "adjustment" and r.original_line_id is not None]
    originals = source.frozen_claims(db, t, sorted({r.original_line_id for r in returns}))
    compared += len(returns)
    for row in returns:
        orig = originals.get(row.original_line_id)
        if orig is None:
            continue
        # the return may be re-imported (new line version): earlier reversals of any version of it count
        done = db.scalar(
            text(
                "WITH RECURSIVE anc(id) AS (SELECT CAST(:ret AS uuid) UNION SELECT p.id FROM sales_document_lines p "
                "JOIN anc ON p.tenant_id = :t AND p.superseded_by_id = anc.id) "
                "SELECT COALESCE(sum(a.amount), 0) FROM claim_adjustments a "
                "JOIN calculation_results r2 ON r2.tenant_id = a.tenant_id AND r2.id = a.calculation_result_id "
                "WHERE a.tenant_id = :t AND a.adjustment_type = 'return_reversal' AND a.claim_line_id = :cl "
                "AND a.status <> 'rejected' AND r2.sales_document_line_id IN (SELECT id FROM anc)"
            ),
            {"t": t, "cl": orig.claim_line_id, "ret": row.sales_document_line_id},
        )
        amount = row.claim_amount - done
        if amount == 0:
            continue
        reason = (
            f"Return {row.document_number}/{row.line_number} after the original was claimed: "
            f"reversal due {row.claim_amount}, already proposed {done}, proposed {amount}"
        )
        if CANNOT_BE_NET_CLAIMED in row.flags:
            reason += "; the manufacturer does not accept negative claims: cannot be net-claimed, discuss with the manufacturer"
        a = _insert(
            db,
            who,
            run_id,
            manufacturer_id=orig.manufacturer_id,
            adjustment_type="return_reversal",
            claim_line_id=orig.claim_line_id,
            calculation_result_id=row.id,
            amount=amount,
            reason=reason,
        )
        if a is not None:
            made.append(a)

    # rebate_clawback (§5.3 R2): a negative accrual delta
    for e in db.execute(
        text(
            "SELECT id, manufacturer_id, delta, external_number, period_start, period_end "
            "FROM rebate_accrual_entries WHERE tenant_id = :t AND run_id = :r AND delta < 0 ORDER BY id"
        ),
        {"t": t, "r": run_id},
    ):
        compared += 1
        a = _insert(
            db,
            who,
            run_id,
            manufacturer_id=e.manufacturer_id,
            adjustment_type="rebate_clawback",
            rebate_accrual_entry_id=e.id,
            amount=e.delta,
            reason=f"Rebate {e.external_number} {e.period_start} to {e.period_end}: accrued amount fell by {-e.delta}",
        )
        if a is not None:
            made.append(a)

    for a in made:
        _audit(db, who, audit.Action.CLAIM_ADJUSTMENT_PROPOSED, a, after=view(a))
    service.audit_proposals(db, who, run_id, len(made))
    return compared, made
