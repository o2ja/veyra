"""Reconciliation of an accepted remittance (BUSINESS_LOGIC.md §10.2-§10.3): the matching run, the human decisions on
matches, and finalisation into variance findings.

Matching and variance rules live in ``rebate_matching``; this module loads the data, calls it, persists the result
and writes the audit events in the same transaction. Claim data comes from the ``claims`` module's readers."""

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, InvalidState, NotFound, PreconditionFailed, ValidationFailed
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError, enqueue, job
from rebate.modules import audit, claims, masterdata, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.masterdata import Who, decode_cursor, encode_cursor, product_alias_rows
from rebate.modules.reconciliation import ingest
from rebate.modules.reconciliation.models import ReconciliationMatch, RemittanceDocument, RemittanceLine
from rebate_matching.config import DEFAULT_CONFIG
from rebate_matching.engine import (
    InvariantViolation,
    confirm_suggestion,
    manual_adjustment_match,
    manual_match,
    reconcile,
    validate_matches,
)
from rebate_matching.models import (
    Adjustment,
    Allocation,
    ClaimLine,
    Match,
    Method,
    Status,
)
from rebate_matching.models import (
    RemittanceLine as RemitLine,
)
from rebate_matching.normalise import normalise_sku
from rebate_matching.variance import (
    UnclassifiableVariance,
    classify_claim_line,
    classify_unmatched_payment,
    paid_for,
)

JOB = "remittance_reconcile"
ZERO = Decimal(0)


class MatchInvariant(Conflict):
    code = "match_invariant"


class UncategorisedLines(Conflict):
    code = "uncategorised_lines"
    message = "Match or categorise every remittance line before finalising."


class LineHasMatch(Conflict):
    code = "line_matched"
    message = "A line with a confirmed match cannot be categorised."


class LineCategorised(Conflict):
    code = "line_categorised"
    message = "A categorised line cannot be matched; the categorisation is final."


class UnresolvedSuggestions(Conflict):
    code = "unresolved_suggestions"
    message = "Confirm or reject every suggested match before finalising."


# ---- finalisation interface (S9) ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One §10.3 variance finding; ``amount`` is positive."""

    case_type: str
    amount: Decimal
    reason_code: str
    claim_line_id: UUID | None
    remittance_line_id: UUID | None
    claimed: Decimal | None
    paid: Decimal
    external_reason_code: str | None


@dataclass(frozen=True)
class FinalisedRemittance:
    remittance_id: UUID
    manufacturer_id: UUID
    document_number: str | None
    document_date: date | None
    payment_date: date | None
    channel: str
    findings: tuple[Finding, ...]


_HOOKS: list[Callable[[Session, Who, FinalisedRemittance], None]] = []


def on_finalise(hook: Callable[[Session, Who, FinalisedRemittance], None]) -> None:
    """Register a hook run inside the finalisation transaction (S9 creates its cases here). Findings repeat: every
    finalisation of a manufacturer's remittance reports ``unpaid`` again for each still-overdue unpaid claim line (and
    nothing deduplicates them here), so a hook must skip a case that already exists (S9 does:
    ``on_conflict_do_nothing`` on the open-case unique index)."""
    _HOOKS.append(hook)


# ---- domain state -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdjustmentItem:
    """An approved adjustment carried by a submitted batch, reconciled as its own item (never added to the original
    claim line, owner decision 2026-11-13). Interim, pending owner decision D-N11: it gets an allocation only when a
    person confirms a manual match with it, and it produces no finding."""

    id: str
    claim_line_id: str
    batch_id: UUID
    amount: Decimal


@dataclass
class State:
    doc: RemittanceDocument
    remits: dict[str, RemitLine]
    remit_ids: dict[str, UUID]  # str id -> remittance line id (same, typed)
    claims: dict[str, ClaimLine]  # every submitted, unreleased claim line of the manufacturer
    submitted: dict[str, date | None]
    elsewhere: set[str]  # claim lines with a confirmed allocation from another remittance
    adjustment_line: dict[str, str]  # adjustment id -> claim line id (every carried adjustment)
    adjustments: dict[
        str, AdjustmentItem
    ]  # the adjustments carried by ANOTHER batch than their original line
    rows: list[ReconciliationMatch]
    paid_elsewhere: dict[str, Decimal]  # claim line id -> Σ confirmed allocations from other remittances
    categorised: set[str]  # remittance line ids a person categorised
    tolerance: Decimal

    def pool(self) -> list[ClaimLine]:
        return [c for i, c in self.claims.items() if i not in self.elsewhere]

    def topup(self) -> list[ClaimLine]:
        """Lines a previous remittance paid in part and that are still short: offered to T1/T2 only. Their
        same-batch adjustments were allocated by the first payment, so they are not split again."""
        return [
            replace(c, adjustments=())
            for i, c in self.claims.items()
            if i in self.elsewhere and c.amount - self.paid_elsewhere.get(i, ZERO) > self.tolerance
        ]


def _aliases(db: Session, tenant_id: UUID) -> dict[str, str]:
    return {normalise_sku(r.alias): normalise_sku(r.sku) for r in product_alias_rows(db, tenant_id)}


def _claims(
    db: Session, doc: RemittanceDocument
) -> tuple[dict[str, ClaimLine], dict[str, date | None], dict[str, str], dict[str, AdjustmentItem]]:
    adj = claims.included_adjustments(db, doc.tenant_id, doc.manufacturer_id)
    lines = claims.submitted_claim_lines(db, doc.tenant_id, doc.manufacturer_id)
    batch_of = {str(r.id): r.batch_id for r in lines}
    by_line: dict[str, list[Adjustment]] = defaultdict(list)
    separate: dict[str, AdjustmentItem] = {}
    for a in adj:
        line_id = str(a.claim_line_id)
        if batch_of.get(line_id) == a.batch_id:  # carried by its original line's batch: part of that line
            by_line[line_id].append(Adjustment(str(a.id), a.amount))
        else:
            separate[str(a.id)] = AdjustmentItem(str(a.id), line_id, a.batch_id, a.amount)
    by_id, submitted = {}, {}
    for r in lines:
        cid = str(r.id)
        by_id[cid] = ClaimLine(
            id=cid,
            manufacturer=str(doc.manufacturer_id),
            claim_reference=r.claim_reference,
            invoice_no=r.invoice_no or "",
            invoice_line=r.invoice_line,
            amount=r.claim_amount,
            sku=r.sku,
            quantity=r.quantity,
            date=r.invoice_date,
            adjustments=tuple(by_line.get(cid, ())),
        )
        submitted[cid] = r.submitted_at
    return by_id, submitted, {str(a.id): str(a.claim_line_id) for a in adj}, separate


def _state(db: Session, doc: RemittanceDocument) -> State:
    t = doc.tenant_id
    lines = list(
        db.scalars(
            select(RemittanceLine)
            .where(RemittanceLine.tenant_id == t, RemittanceLine.remittance_document_id == doc.id)
            .order_by(RemittanceLine.line_number)
        )
    )
    remits = {
        str(r.id): RemitLine(
            id=str(r.id),
            manufacturer=str(doc.manufacturer_id),
            amount_paid=r.amount_paid,
            claim_reference=r.claim_reference,
            invoice_no=r.invoice_number,
            invoice_line=r.invoice_line_number,
            sku=r.sku_raw,
            quantity=r.quantity,
            reason_code=r.external_reason_code,
        )
        for r in lines
    }
    claims, submitted, adj_line, separate = _claims(db, doc)
    rows = list(
        db.scalars(
            select(ReconciliationMatch)
            .join(
                RemittanceLine,
                (RemittanceLine.tenant_id == ReconciliationMatch.tenant_id)
                & (RemittanceLine.id == ReconciliationMatch.remittance_line_id),
            )
            .where(ReconciliationMatch.tenant_id == t, RemittanceLine.remittance_document_id == doc.id)
            .order_by(ReconciliationMatch.id)
        )
    )
    other = db.execute(
        select(ReconciliationMatch.claim_line_id, func.sum(ReconciliationMatch.allocated_amount))
        .join(
            RemittanceLine,
            (RemittanceLine.tenant_id == ReconciliationMatch.tenant_id)
            & (RemittanceLine.id == ReconciliationMatch.remittance_line_id),
        )
        .where(
            ReconciliationMatch.tenant_id == t,
            ReconciliationMatch.status == "confirmed",
            ReconciliationMatch.claim_line_id.is_not(None),
            RemittanceLine.remittance_document_id != doc.id,
        )
        .group_by(ReconciliationMatch.claim_line_id)
    )
    paid_elsewhere = {str(c): s for c, s in other}
    try:
        tolerance = masterdata.get_manufacturer_settings(db, t, doc.manufacturer_id).variance_tolerance
    except NotFound:  # no settings row: the documented default
        tolerance = Decimal("0.01")
    return State(
        doc, remits, {i: UUID(i) for i in remits}, claims, submitted, set(paid_elsewhere), adj_line, separate,
        rows, paid_elsewhere, {str(r.id) for r in lines if r.category}, tolerance,
    )  # fmt: skip


def _matches(
    rows: Sequence[ReconciliationMatch], adj_line: dict[str, str], status: str | None = None
) -> list[Match]:
    """Persisted allocation rows -> domain matches (one per ``match_id``)."""
    groups: dict[UUID, list[ReconciliationMatch]] = defaultdict(list)
    for r in rows:
        if status is None or r.status == status:
            groups[r.match_id].append(r)
    out = []
    for g in groups.values():
        allocs = tuple(
            Allocation(str(r.claim_line_id), r.allocated_amount)
            if r.claim_line_id is not None
            else Allocation(
                adj_line[str(r.claim_adjustment_id)], r.allocated_amount, str(r.claim_adjustment_id)
            )
            for r in g
        )
        out.append(
            Match(
                str(g[0].remittance_line_id),
                Method(g[0].method),
                Status(g[0].status) if g[0].status != "rejected" else Status.SUGGESTED,
                allocs,
                dict(g[0].evidence),
                False,
            )
        )
    return out


def _score(m: Match) -> Decimal | None:
    raw = m.evidence.get("score")
    return None if raw is None else Decimal(str(raw)).quantize(Decimal("0.0001"))


def _insert(
    db: Session, st: State, matches: Sequence[Match], run_id: UUID | None, user_id: UUID | None
) -> list[UUID]:
    now = clock.now()
    rows = []
    ids = []
    for m in matches:
        mid = new_id()
        ids.append(mid)
        confirmed = m.status is Status.CONFIRMED
        evidence = json.loads(json.dumps(m.evidence, default=str))
        if confirmed and m.method is not Method.MANUAL:
            evidence["auto_confirmed"] = True
        for a in m.allocations:
            rows.append(
                {
                    "tenant_id": st.doc.tenant_id,
                    "id": new_id(),
                    "match_id": mid,
                    "remittance_line_id": UUID(m.remittance_line_id),
                    "claim_line_id": None if a.adjustment_ref else UUID(a.claim_line_id),
                    "claim_adjustment_id": UUID(a.adjustment_ref) if a.adjustment_ref else None,
                    "allocated_amount": a.amount,
                    "method": m.method.value,
                    "score": _score(m),
                    "evidence": evidence,
                    "status": m.status.value,
                    "matched_by_run_id": run_id,
                    "confirmed_by": user_id if confirmed else None,
                    "confirmed_at": now if confirmed else None,
                    "created_at": now,
                }
            )
    if rows:
        db.execute(insert(ReconciliationMatch), rows)
    return ids


def _delete_suggested(db: Session, st: State, line_ids: Sequence[UUID] | None = None) -> None:
    stmt = delete(ReconciliationMatch).where(
        ReconciliationMatch.tenant_id == st.doc.tenant_id,
        ReconciliationMatch.status == "suggested",
        ReconciliationMatch.remittance_line_id.in_(
            list(line_ids if line_ids is not None else st.remit_ids.values())
        ),
    )
    db.execute(stmt)


def _locked(db: Session, who_tenant: UUID, remittance_id: UUID) -> RemittanceDocument:
    doc = ingest.get(db, who_tenant, remittance_id, lock=True)
    if doc.status != "accepted":
        raise InvalidState("Only an accepted remittance can be reconciled.")
    return doc


# ---- the matching run -----------------------------------------------------------------------------------------


def request_run(db: Session, who: Who, remittance_id: UUID) -> UUID:
    doc = _locked(db, who.tenant_id, remittance_id)
    job_id = enqueue(
        db,
        JOB,
        tenant_id=who.tenant_id,
        payload={"remittance_id": str(doc.id)},
        created_by=who.user_id,
        dedupe_key=f"{JOB}:{new_id()}",
    )
    if job_id is None:
        raise RuntimeError("job was not enqueued")
    return job_id


def run(
    db: Session, tenant_id: UUID, remittance_id: UUID, run_id: UUID, user_id: UUID, meta: dict[str, Any]
) -> dict[str, Any]:
    """Match the remittance's lines against the manufacturer's claim lines. Confirmed matches are kept untouched,
    suggestions are recomputed (a suggestion a person rejected before is not suggested again)."""
    doc = _locked(db, tenant_id, remittance_id)
    st = _state(db, doc)
    kept = _matches(st.rows, st.adjustment_line, "confirmed")
    rejected = {
        (m.remittance_line_id, m.claim_line_ids)
        for m in _matches([r for r in st.rows if r.status == "rejected"], st.adjustment_line)
    }
    result = reconcile(
        list(st.remits.values()),
        st.pool(),
        existing=kept,
        config=DEFAULT_CONFIG,
        sku_aliases=_aliases(db, tenant_id),
        topup_lines=st.topup(),
    )
    new = [
        m
        for m in result.matches
        if m not in kept and (m.remittance_line_id, m.claim_line_ids) not in rejected
    ]
    _delete_suggested(db, st)
    _insert(db, st, new, run_id, user_id)
    summary = {
        "kept_confirmed": len(kept),
        "new_confirmed": sum(m.status is Status.CONFIRMED for m in new),
        "new_suggested": sum(m.status is Status.SUGGESTED for m in new),
        "unmatched_lines": len(result.unmatched_remittance_ids),
        "notes": sorted({n.code for n in result.notes}),
        "rows": sum(len(m.allocations) for m in new),
        "config": json.loads(json.dumps(asdict(DEFAULT_CONFIG), default=str)),
    }
    audit.record(
        db, tenant_id=tenant_id, actor=Actor("user", user_id), action=Action.RECONCILIATION_RUN,
        entity_type="remittance", entity_id=doc.id, meta=meta, after={"run": summary},
    )  # fmt: skip
    return summary


@job(JOB, tenant_scoped=True, max_attempts=3)
def remittance_reconcile(db: Session, ctx: JobContext) -> None:
    if ctx.tenant_id is None or ctx.created_by is None:
        raise PermanentError("the reconciliation job needs a tenant and a user")
    run(db, ctx.tenant_id, UUID(ctx.payload["remittance_id"]), ctx.id, ctx.created_by, {"request_id": "job"})


# ---- decisions --------------------------------------------------------------------------------------------------


def _group(db: Session, tenant_id: UUID, match_id: UUID) -> list[ReconciliationMatch]:
    rows = list(
        db.scalars(
            select(ReconciliationMatch)
            .where(ReconciliationMatch.tenant_id == tenant_id, ReconciliationMatch.match_id == match_id)
            .order_by(ReconciliationMatch.id)
            .with_for_update()
        )
    )
    if not rows:
        raise NotFound()
    return rows


def _doc_of_line(db: Session, tenant_id: UUID, remittance_line_id: UUID) -> UUID:
    doc_id = db.scalar(
        select(RemittanceLine.remittance_document_id).where(
            RemittanceLine.tenant_id == tenant_id, RemittanceLine.id == remittance_line_id
        )
    )
    if doc_id is None:
        raise NotFound()
    return doc_id


def match_audit(rows: Sequence[ReconciliationMatch], status: str) -> dict[str, Any]:
    return {
        "remittance_line_id": str(rows[0].remittance_line_id),
        "method": rows[0].method,
        "status": status,
        "allocated_amount": str(sum((r.allocated_amount for r in rows), ZERO)),
    }


def decide(db: Session, who: Who, match_id: UUID, revision: int, decision: str) -> list[ReconciliationMatch]:
    """``decision``: ``confirm`` or ``reject`` a suggested match (``If-Match`` revision)."""
    t = who.tenant_id
    probe = _group(db, t, match_id)
    doc = _locked(db, t, _doc_of_line(db, t, probe[0].remittance_line_id))
    rows = _group(db, t, match_id)  # re-read under the document lock
    if rows[0].status != "suggested":
        raise InvalidState("This match was already decided.")
    if rows[0].revision != revision:
        raise PreconditionFailed()
    now = clock.now()
    if decision == "confirm":
        st = _state(db, doc)
        if str(rows[0].remittance_line_id) in st.categorised:
            raise LineCategorised()
        group = confirm_suggestion(_matches(rows, st.adjustment_line)[0])
        confirmed = [*_matches(st.rows, st.adjustment_line, "confirmed"), group]
        try:
            validate_matches(confirmed, st.remits, st.claims)
        except InvariantViolation as exc:
            raise MatchInvariant(str(exc)) from None
        values = {"status": "confirmed", "confirmed_by": who.user_id, "confirmed_at": now}
        action = Action.MATCH_CONFIRMED
    else:
        values = {"status": "rejected", "rejected_by": who.user_id, "rejected_at": now}
        action = Action.MATCH_REJECTED
    db.execute(
        update(ReconciliationMatch)
        .where(ReconciliationMatch.tenant_id == t, ReconciliationMatch.match_id == match_id)
        .values(revision=revision + 1, **values)
    )
    db.expire_all()
    done = _group(db, t, match_id)
    audit.record(
        db, tenant_id=t, actor=Actor("user", who.user_id), action=action, entity_type="reconciliation_match",
        entity_id=match_id, meta=who.meta, after=match_audit(done, done[0].status) | {"remittance_id": doc.id},
    )  # fmt: skip
    return done


def _invalid(field: str, code: str, message: str) -> ValidationFailed:
    return ValidationFailed(details=[{"field": field, "code": code, "message": message}])


def manual(
    db: Session,
    who: Who,
    remittance_line_id: UUID,
    claim_line_id: UUID | None,
    amount: Decimal,
    claim_adjustment_id: UUID | None = None,
) -> list[ReconciliationMatch]:
    """A person's match of one remittance line to a claim line, or (interim, pending D-N11) to an adjustment item
    carried by another batch. ``amount`` is 0.00 only on a line that was paid 0.00 (it makes the claim line
    ``rejected``); on an adjustment item it carries the adjustment's sign."""
    t = who.tenant_id
    if (claim_line_id is None) == (claim_adjustment_id is None):
        raise _invalid("claim_line_id", "exactly_one", "Give either claim_line_id or claim_adjustment_id.")
    doc = _locked(db, t, _doc_of_line(db, t, remittance_line_id))
    st = _state(db, doc)
    if str(remittance_line_id) in st.categorised:
        raise LineCategorised()
    rem = st.remits[str(remittance_line_id)]
    if claim_adjustment_id is None:
        if amount < 0 or (amount == 0 and rem.amount_paid != 0):
            raise _invalid(
                "allocated_amount",
                "invalid",
                "A claim line gets a positive amount (0.00 only for a line paid 0.00).",
            )
    adjustment = st.adjustments.get(str(claim_adjustment_id)) if claim_adjustment_id else None
    if claim_adjustment_id is not None:
        if adjustment is None:
            raise _invalid(
                "claim_adjustment_id",
                "not_found",
                "Not an adjustment carried by another submitted batch of this manufacturer.",
            )
        if amount == 0 or (amount < 0) != (adjustment.amount < 0) or abs(amount) > abs(adjustment.amount):
            raise _invalid(
                "allocated_amount",
                "invalid",
                "The amount must have the adjustment's sign and not exceed its amount.",
            )
    claim = st.claims.get(adjustment.claim_line_id if adjustment else str(claim_line_id))
    if claim is None:
        raise _invalid("claim_line_id", "not_found", "Not a submitted claim line of this manufacturer.")
    evidence: dict[str, object] = {"method": "manual", "entered_by": str(who.user_id)}
    try:
        match = (
            manual_adjustment_match(rem, claim, adjustment.id, amount, evidence)
            if adjustment
            else manual_match(rem, [(claim, amount)], evidence)
        )
        _delete_suggested(db, st, [remittance_line_id])
        db.flush()
        keep = [r for r in st.rows if r.status != "suggested" or r.remittance_line_id != remittance_line_id]
        validate_matches([*_matches(keep, st.adjustment_line, "confirmed"), match], st.remits, st.claims)
    except InvariantViolation as exc:
        raise MatchInvariant(str(exc)) from None
    (match_id,) = _insert(db, st, [match], None, who.user_id)
    db.flush()
    group = _group(db, t, match_id)
    audit.record(
        db, tenant_id=t, actor=Actor("user", who.user_id), action=Action.MATCH_CREATED, entity_type="reconciliation_match",
        entity_id=group[0].match_id, meta=who.meta, after=match_audit(group, "confirmed") | {"remittance_id": doc.id},
    )  # fmt: skip
    return group


def categorise(db: Session, who: Who, remittance_line_id: UUID, category: str) -> RemittanceLine:
    """A person's decision that an unmatched line is an ``unmatched_payment`` (BUSINESS_LOGIC §10.3). Once; refused
    (409) for a line with a confirmed match or one already categorised."""
    t = who.tenant_id
    doc = _locked(db, t, _doc_of_line(db, t, remittance_line_id))
    line = db.scalar(
        select(RemittanceLine)
        .where(RemittanceLine.tenant_id == t, RemittanceLine.id == remittance_line_id)
        .with_for_update()
    )
    if line is None:
        raise NotFound()
    if line.category is not None:
        raise InvalidState("This line is already categorised.")
    confirmed = db.scalar(
        select(func.count())
        .select_from(ReconciliationMatch)
        .where(
            ReconciliationMatch.tenant_id == t,
            ReconciliationMatch.remittance_line_id == remittance_line_id,
            ReconciliationMatch.status == "confirmed",
        )
    )
    if confirmed:
        raise LineHasMatch()
    line.category, line.categorised_by, line.categorised_at = category, who.user_id, clock.now()
    db.flush()
    audit.record(
        db, tenant_id=t, actor=Actor("user", who.user_id), action=Action.REMITTANCE_LINE_CATEGORISED,
        entity_type="remittance_line", entity_id=line.id, meta=who.meta,
        after={"remittance_id": doc.id, "line_number": line.line_number, "category": category,
               "amount_paid": str(line.amount_paid)},
    )  # fmt: skip
    return line


# ---- reads ------------------------------------------------------------------------------------------------------


def list_matches(
    db: Session,
    tenant_id: UUID,
    *,
    remittance_id: UUID | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[list[ReconciliationMatch]], str | None]:
    """Matches (allocation rows grouped), oldest first, ``limit`` groups per page."""
    after = decode_cursor(cursor)
    stmt = select(ReconciliationMatch.match_id).where(ReconciliationMatch.tenant_id == tenant_id)
    if remittance_id is not None:
        ingest.get(db, tenant_id, remittance_id)
        stmt = stmt.join(
            RemittanceLine,
            (RemittanceLine.tenant_id == ReconciliationMatch.tenant_id)
            & (RemittanceLine.id == ReconciliationMatch.remittance_line_id),
        ).where(RemittanceLine.remittance_document_id == remittance_id)
    if status is not None:
        stmt = stmt.where(ReconciliationMatch.status == status)
    if after is not None:
        stmt = stmt.where(ReconciliationMatch.match_id > after)
    ids = list(
        db.scalars(
            stmt.group_by(ReconciliationMatch.match_id)
            .order_by(ReconciliationMatch.match_id)
            .limit(limit + 1)
        )
    )
    more = len(ids) > limit
    ids = ids[:limit]
    rows = db.scalars(
        select(ReconciliationMatch)
        .where(ReconciliationMatch.tenant_id == tenant_id, ReconciliationMatch.match_id.in_(ids))
        .order_by(ReconciliationMatch.id)
    )
    groups: dict[UUID, list[ReconciliationMatch]] = {i: [] for i in ids}
    for r in rows:
        groups[r.match_id].append(r)
    return list(groups.values()), encode_cursor(ids[-1]) if more and ids else None


def get_match(db: Session, tenant_id: UUID, match_id: UUID) -> list[ReconciliationMatch]:
    rows = list(
        db.scalars(
            select(ReconciliationMatch)
            .where(ReconciliationMatch.tenant_id == tenant_id, ReconciliationMatch.match_id == match_id)
            .order_by(ReconciliationMatch.id)
        )
    )
    if not rows:
        raise NotFound()
    return rows


# ---- finalisation ---------------------------------------------------------------------------------------------


def _today(db: Session, tenant_id: UUID) -> date:
    zone = tenancy.get_settings(db, tenant_id).business_timezone
    return clock.now().astimezone(ZoneInfo(zone)).date()


def _confirmed_everywhere(db: Session, st: State) -> tuple[list[Match], dict[str, RemitLine], dict[str, str]]:
    """Every confirmed match touching the manufacturer's claim lines (any remittance), their remittance lines and the
    external -> controlled reason mapping recorded on those lines at acceptance."""
    t = st.doc.tenant_id
    rows = list(
        db.execute(
            select(ReconciliationMatch, RemittanceLine)
            .join(
                RemittanceLine,
                (RemittanceLine.tenant_id == ReconciliationMatch.tenant_id)
                & (RemittanceLine.id == ReconciliationMatch.remittance_line_id),
            )
            .join(
                RemittanceDocument,
                (RemittanceDocument.tenant_id == RemittanceLine.tenant_id)
                & (RemittanceDocument.id == RemittanceLine.remittance_document_id),
            )
            .where(
                ReconciliationMatch.tenant_id == t,
                ReconciliationMatch.status == "confirmed",
                RemittanceDocument.manufacturer_id == st.doc.manufacturer_id,
            )
            .order_by(ReconciliationMatch.id)
        )
    )
    remits = dict(st.remits)
    mapping: dict[str, str] = {}
    for _m, line in rows:
        rid = str(line.id)
        if rid not in remits:
            remits[rid] = RemitLine(
                id=rid, manufacturer=str(st.doc.manufacturer_id), amount_paid=line.amount_paid,
                reason_code=line.external_reason_code,
            )  # fmt: skip
        if line.external_reason_code and line.reason_code:
            mapping[line.external_reason_code] = line.reason_code
    return _matches([m for m, _l in rows], st.adjustment_line), remits, mapping


def finalise(db: Session, who: Who, remittance_id: UUID) -> FinalisedRemittance:
    t = who.tenant_id
    doc = _locked(db, t, remittance_id)
    st = _state(db, doc)
    pending = sum(1 for r in st.rows if r.status == "suggested")
    if pending:
        raise UnresolvedSuggestions(
            details=[{"field": "matches", "code": "suggested", "message": f"{pending} rows"}]
        )
    matches, remits, mapping = _confirmed_everywhere(db, st)
    own = [m for m in matches if m.remittance_line_id in st.remits]
    decided = {m.remittance_line_id for m in own} | st.categorised
    open_lines = sorted(rid for rid in st.remits if rid not in decided)
    if open_lines:
        raise UncategorisedLines(
            details=[{"field": "lines", "code": "uncategorised", "message": f"{len(open_lines)} lines"}]
        )
    touched = {cid for m in own for cid in m.claim_line_ids}
    try:
        mset = masterdata.get_manufacturer_settings(db, t, doc.manufacturer_id)
        tolerance, lag = mset.variance_tolerance, mset.payment_lag_days_expected
    except NotFound:  # no settings row: the documented defaults
        tolerance, lag = Decimal("0.01"), None
    today = _today(db, t)
    paid_anywhere = {cid for m in matches for cid in m.claim_line_ids}
    findings: list[Finding] = []
    try:
        for cid, claim in sorted(st.claims.items(), key=lambda kv: kv[1].claim_reference):
            if cid not in touched and cid in paid_anywhere:
                continue  # settled by another remittance
            case = classify_claim_line(
                claim, matches, remits, mapping,
                tolerance=tolerance, as_of=today, submitted_at=st.submitted[cid], payment_lag_days=lag,
            )  # fmt: skip
            if case is None:
                continue
            via = next((m for m in matches if cid in m.claim_line_ids), None)
            ext = remits[via.remittance_line_id].reason_code if via else None
            findings.append(
                Finding(
                    case.case_type,
                    case.amount,
                    case.reason_code,
                    UUID(cid),
                    UUID(via.remittance_line_id) if via else None,
                    claim.amount,
                    paid_for(cid, matches),
                    ext,
                )
            )
        for rid, line in st.remits.items():
            case = classify_unmatched_payment(line, own, categorised=rid in st.categorised)
            if case is not None:
                findings.append(
                    Finding(
                        case.case_type,
                        case.amount,
                        case.reason_code,
                        None,
                        UUID(rid),
                        None,
                        line.amount_paid,
                        line.reason_code,
                    )
                )
    except UnclassifiableVariance as exc:
        raise MatchInvariant(str(exc)) from None
    doc.status, doc.finalised_by, doc.finalised_at = "reconciled", who.user_id, clock.now()
    db.flush()
    result = FinalisedRemittance(
        doc.id,
        doc.manufacturer_id,
        doc.document_number,
        doc.document_date,
        doc.payment_date,
        doc.channel,
        tuple(findings),
    )
    audit.record(
        db, tenant_id=t, actor=Actor("user", who.user_id), action=Action.REMITTANCE_FINALISED, entity_type="remittance",
        entity_id=doc.id, meta=who.meta,
        after={"status": "reconciled", "findings": [f"{f.case_type}:{f.reason_code}:{f.amount}" for f in findings]},
    )  # fmt: skip
    for hook in _HOOKS:
        hook(db, who, result)
    return result
