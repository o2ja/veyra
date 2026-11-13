"""Tiered matching of remittance lines to claim lines (BUSINESS_LOGIC.md §10.2).

``reconcile`` is pure and deterministic: the output does not depend on input order (everything is processed
in id order, and each tier accepts a pair only if it is unambiguous from BOTH sides).

Rules (documented decisions):
* ``auto_confirm`` is True only for exact tiers: T1, T2, and T3 with exactly equal amounts. T4/T5 never.
* Ambiguity (two candidates for one line, or two lines for one claim) never matches in that tier.
* 1:1 matches allocate ``amount_paid`` (so overpayment stays visible to §10.3); the adjustments a claim line carries
  IN ITS OWN BATCH are allocated at face value and the remainder goes to the claim itself (ground truth: CL 250 /
  ADJ -50 / paid 200). ``ClaimLine.net_amount`` is the payment expected for matching only; §10.3 ``claimed`` is
  ``ClaimLine.amount``. An adjustment carried by another batch is not on the line and is never allocated here.
* A claim line with any confirmed allocation is consumed and not offered to automatic tiers again, except a
  ``topup`` line (partly paid by an earlier remittance), which T1/T2 (exact identifiers) may still pick.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal

from rebate_matching.assignment import maximise_assignment, unique_subset_sum
from rebate_matching.config import DEFAULT_CONFIG, MatchConfig
from rebate_matching.models import (
    Allocation,
    ClaimLine,
    Match,
    MatchResult,
    Method,
    Note,
    RemittanceLine,
    Status,
)
from rebate_matching.normalise import normalise_ref, normalise_sku
from rebate_matching.scoring import fuzzy_score


class InvariantViolation(Exception):
    """A match set breaks a §10.2 invariant. Always a defect or invalid manual input; never swallowed."""


def allocate(claim: ClaimLine, paid: Decimal) -> tuple[Allocation, ...]:
    """Split ``paid`` over a claim line: adjustments at face value, remainder on the claim itself."""
    adj = tuple(Allocation(claim.id, a.amount, a.ref) for a in claim.adjustments)
    rest = paid - sum((a.amount for a in claim.adjustments), Decimal(0))
    return (Allocation(claim.id, rest), *adj)


def _is_exact(claim: ClaimLine, paid: Decimal) -> bool:
    return claim.net_amount == paid


def _one_to_one(
    rem: RemittanceLine,
    claim: ClaimLine,
    method: Method,
    status: Status,
    auto: bool,
    evidence: dict[str, object],
) -> Match:
    return Match(rem.id, method, status, allocate(claim, rem.amount_paid), dict(evidence), auto)


def _resolve(candidates: Mapping[str, list[str]]) -> dict[str, str]:
    """Keep (remittance -> claim) only if the remittance has one candidate and the claim is wanted by one remittance."""
    wanted: dict[str, int] = defaultdict(int)
    for cl in candidates.values():
        for c in cl:
            wanted[c] += 1
    return {r: cl[0] for r, cl in candidates.items() if len(cl) == 1 and wanted[cl[0]] == 1}


def tolerance_wide(claim: ClaimLine, cfg: MatchConfig) -> Decimal:
    return max(abs(claim.net_amount) * cfg.tolerance_wide_pct, cfg.tolerance_wide_abs)


def validate_matches(
    matches: Sequence[Match], remits: Mapping[str, RemittanceLine], claims: Mapping[str, ClaimLine]
) -> None:
    """§10.2 invariants: same manufacturer; per remittance line, Σ allocations <= amount_paid, where an allocation to an
    adjustment item carried by ANOTHER batch (one the claim line does not carry in ``adjustments``) is not counted: it is
    not cash on a claim line and must not free room for claim lines (owner decision 2026-11-13; interim, D-N11)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for m in matches:
        rem = remits[m.remittance_line_id]
        for a in m.allocations:
            if claims[a.claim_line_id].manufacturer != rem.manufacturer:
                raise InvariantViolation(
                    f"match {m.remittance_line_id} references another manufacturer's claim line"
                )
        for a in m.allocations:
            claim = claims[a.claim_line_id]
            if a.adjustment_ref is None or any(adj.ref == a.adjustment_ref for adj in claim.adjustments):
                totals[rem.id] += a.amount
    for rid, total in totals.items():
        if total > remits[rid].amount_paid:
            raise InvariantViolation(
                f"allocations {total} exceed amount_paid {remits[rid].amount_paid} on {rid}"
            )


def manual_match(
    rem: RemittanceLine,
    allocations: Sequence[tuple[ClaimLine, Decimal]],
    evidence: dict[str, object] | None = None,
) -> Match:
    """A user's selection: always ``confirmed``, never ``auto_confirm``. Validates manufacturer and Σ <= paid."""
    if not allocations:
        raise InvariantViolation("manual match needs at least one allocation")
    total = Decimal(0)
    out = []
    for claim, amount in allocations:
        if claim.manufacturer != rem.manufacturer:
            raise InvariantViolation("manual match across manufacturers")
        total += amount
        out.append(Allocation(claim.id, amount))
    if total > rem.amount_paid:
        raise InvariantViolation(f"allocations {total} exceed amount_paid {rem.amount_paid}")
    return Match(rem.id, Method.MANUAL, Status.CONFIRMED, tuple(out), dict(evidence or {}), False)


def manual_adjustment_match(
    rem: RemittanceLine,
    claim: ClaimLine,
    adjustment_ref: str,
    amount: Decimal,
    evidence: dict[str, object] | None = None,
) -> Match:
    """A user's confirmation that ``amount`` of a remittance line pays an adjustment item (never automatic: an
    adjustment is reconciled as its own item, pending owner decision D-N11). ``claim`` is the adjusted line."""
    if claim.manufacturer != rem.manufacturer:
        raise InvariantViolation("manual match across manufacturers")
    if amount > rem.amount_paid:
        raise InvariantViolation(f"allocations {amount} exceed amount_paid {rem.amount_paid}")
    alloc = Allocation(claim.id, amount, adjustment_ref)
    return Match(rem.id, Method.MANUAL, Status.CONFIRMED, (alloc,), dict(evidence or {}), False)


def confirm_suggestion(match: Match) -> Match:
    """A user accepts a suggested match: same method and allocations, status ``confirmed``, not auto."""
    return Match(
        match.remittance_line_id, match.method, Status.CONFIRMED, match.allocations, match.evidence, False
    )


def reconcile(
    remittance_lines: Sequence[RemittanceLine],
    claim_lines: Sequence[ClaimLine],
    *,
    existing: Sequence[Match] = (),
    config: MatchConfig = DEFAULT_CONFIG,
    sku_aliases: Mapping[str, str] | None = None,
    topup_lines: Sequence[ClaimLine] = (),
) -> MatchResult:
    """Run T1..T5 over one remittance document's lines and the manufacturer's claim lines.

    ``topup_lines``: claim lines already partly paid by another remittance and still short; only T1 and T2 (exact
    identifiers) may match them, so a top-up payment finds its line without ever guessing (T3-T5 skip them).

    ``existing``: matches from a previous run. Confirmed ones are kept untouched and their lines/claims are
    excluded from matching; suggested ones are discarded and recomputed.
    """
    aliases = dict(sku_aliases or {})
    remits = {r.id: r for r in sorted(remittance_lines, key=lambda r: r.id)}
    top = {c.id: c for c in sorted(topup_lines, key=lambda c: c.id)}
    claims = {c.id: c for c in sorted([*claim_lines, *top.values()], key=lambda c: c.id)}
    kept = [m for m in existing if m.status is Status.CONFIRMED]
    done_rem = {m.remittance_line_id for m in kept}
    done_claim = {cid for m in kept for cid in m.claim_line_ids}
    pend_rem = [r for r in remits.values() if r.id not in done_rem]
    pend_claim = [c for c in claims.values() if c.id not in done_claim and c.id not in top]
    pend_top = [c for c in top.values() if c.id not in done_claim]
    new: list[Match] = []
    notes: list[Note] = []

    def take(matches: list[Match]) -> None:
        nonlocal pend_rem, pend_claim, pend_top
        r_ids = {m.remittance_line_id for m in matches}
        c_ids = {cid for m in matches for cid in m.claim_line_ids}
        new.extend(matches)
        pend_rem = [r for r in pend_rem if r.id not in r_ids]
        pend_claim = [c for c in pend_claim if c.id not in c_ids]
        pend_top = [c for c in pend_top if c.id not in c_ids]

    take(_tier_exact_reference(pend_rem, sorted([*pend_claim, *pend_top], key=lambda c: c.id)))
    take(_tier_invoice_line(pend_rem, sorted([*pend_claim, *pend_top], key=lambda c: c.id)))
    take(_tier_composite(pend_rem, pend_claim, config, aliases))
    take(_tier_fuzzy(pend_rem, pend_claim, config, aliases, notes))
    take(_tier_subset_sum(pend_rem, pend_claim, config, notes))

    order = {m: i for i, m in enumerate(Method)}
    matches = tuple(sorted([*kept, *new], key=lambda m: (m.remittance_line_id, order[m.method])))
    validate_matches(matches, remits, claims)
    return MatchResult(
        matches,
        tuple(r.id for r in pend_rem),
        tuple(c.id for c in pend_claim),
        tuple(sorted(notes, key=lambda n: (n.remittance_line_id, n.code))),
    )


def _tier_exact_reference(rems: list[RemittanceLine], claims: list[ClaimLine]) -> list[Match]:
    by_ref: dict[str, list[ClaimLine]] = defaultdict(list)
    for c in claims:
        by_ref[normalise_ref(c.claim_reference)].append(c)
    cand: dict[str, list[str]] = {}
    for r in rems:
        key = normalise_ref(r.claim_reference)
        hits = [c.id for c in by_ref.get(key, []) if c.manufacturer == r.manufacturer] if key else []
        if hits:
            cand[r.id] = hits
    return _emit(
        cand,
        rems,
        claims,
        Method.EXACT_REFERENCE,
        lambda r, c: ({"claim_reference": c.claim_reference}, True),
    )


def _tier_invoice_line(rems: list[RemittanceLine], claims: list[ClaimLine]) -> list[Match]:
    by_key: dict[tuple[str, int], list[ClaimLine]] = defaultdict(list)
    for c in claims:
        if c.invoice_line is not None and normalise_ref(c.invoice_no):
            by_key[(normalise_ref(c.invoice_no), c.invoice_line)].append(c)
    cand: dict[str, list[str]] = {}
    for r in rems:
        if r.invoice_line is None or not normalise_ref(r.invoice_no):
            continue
        hits = [
            c.id
            for c in by_key.get((normalise_ref(r.invoice_no), r.invoice_line), [])
            if c.manufacturer == r.manufacturer
        ]
        if hits:
            cand[r.id] = hits
    return _emit(
        cand,
        rems,
        claims,
        Method.INVOICE_LINE,
        lambda r, c: ({"invoice_no": normalise_ref(c.invoice_no), "invoice_line": c.invoice_line}, True),
    )


def _tier_composite(
    rems: list[RemittanceLine], claims: list[ClaimLine], cfg: MatchConfig, aliases: dict[str, str]
) -> list[Match]:
    cand: dict[str, list[str]] = {}
    for r in rems:
        inv, sku = normalise_ref(r.invoice_no), normalise_sku(r.sku, aliases)
        if not inv or not sku or r.quantity is None:
            continue
        hits = [
            c.id
            for c in claims
            if c.manufacturer == r.manufacturer
            and normalise_ref(c.invoice_no) == inv
            and normalise_sku(c.sku, aliases) == sku
            and c.quantity == r.quantity
            and abs(r.amount_paid - c.net_amount) <= tolerance_wide(c, cfg)
        ]
        if hits:
            cand[r.id] = hits

    def ev(r: RemittanceLine, c: ClaimLine) -> tuple[dict[str, object], bool]:
        return (
            {
                "invoice_no": normalise_ref(c.invoice_no),
                "sku": normalise_sku(c.sku, aliases),
                "quantity": str(c.quantity),
                "amount_delta": str(r.amount_paid - c.net_amount),
            },
            _is_exact(c, r.amount_paid),
        )

    return _emit(cand, rems, claims, Method.COMPOSITE, ev)


def _emit(
    cand: dict[str, list[str]],
    rems: list[RemittanceLine],
    claims: list[ClaimLine],
    method: Method,
    evidence: Callable[[RemittanceLine, ClaimLine], tuple[dict[str, object], bool]],
) -> list[Match]:
    """Turn resolved (unambiguous) candidates into matches; ``evidence(r, c) -> (dict, exact)``."""
    rmap = {r.id: r for r in rems}
    cmap = {c.id: c for c in claims}
    out = []
    for rid, cid in sorted(_resolve(cand).items()):
        ev, exact = evidence(rmap[rid], cmap[cid])
        status = Status.CONFIRMED if exact else Status.SUGGESTED
        out.append(_one_to_one(rmap[rid], cmap[cid], method, status, exact, {"method": method.value, **ev}))
    return out


def _tier_fuzzy(
    rems: list[RemittanceLine],
    claims: list[ClaimLine],
    cfg: MatchConfig,
    aliases: dict[str, str],
    notes: list[Note],
) -> list[Match]:
    scores: dict[tuple[str, str], tuple[Decimal, dict[str, str]]] = {}
    for r in rems:
        for c in claims:
            if c.manufacturer == r.manufacturer:
                s = fuzzy_score(r, c, cfg, aliases)
                if s[0] >= cfg.fuzzy_threshold:
                    scores[(r.id, c.id)] = s
    rids = sorted({k[0] for k in scores})
    cids = sorted({k[1] for k in scores})
    if not rids:
        return []
    if max(len(rids), len(cids)) > cfg.max_assignment_dim:
        notes.extend(Note(rid, Method.FUZZY, "assignment_limit_reached") for rid in rids)
        return []
    scale = Decimal(10**6)
    grid = [[int(scores[(r, c)][0] * scale) if (r, c) in scores else 0 for c in cids] for r in rids]
    rmap = {r.id: r for r in rems}
    cmap = {c.id: c for c in claims}
    out = []
    for i, j in maximise_assignment(grid):
        rid, cid = rids[i], cids[j]
        total, comps = scores[(rid, cid)]
        tied = sorted(
            {c for (r2, c) in scores if r2 == rid and c != cid and scores[(r2, c)][0] == total}
            | {r for (r, c2) in scores if c2 == cid and r != rid and scores[(r, c2)][0] == total}
        )
        ev: dict[str, object] = {"method": Method.FUZZY.value, "score": str(total), "components": comps}
        if tied:
            ev["tied_with"] = tied  # equal-scoring alternatives: a human must choose
        out.append(_one_to_one(rmap[rid], cmap[cid], Method.FUZZY, Status.SUGGESTED, False, ev))
    return out


def _tier_subset_sum(
    rems: list[RemittanceLine], claims: list[ClaimLine], cfg: MatchConfig, notes: list[Note]
) -> list[Match]:
    free = list(claims)
    out = []
    for r in rems:
        if r.amount_paid <= 0:
            continue
        pool = [c for c in free if c.manufacturer == r.manufacturer and 0 < c.net_amount <= r.amount_paid]
        status, idx = unique_subset_sum(
            [c.net_amount for c in pool], r.amount_paid, cfg.subset_max_size, cfg.subset_max_combinations
        )
        if status == "limit":
            notes.append(Note(r.id, Method.SUBSET_SUM, "combination_limit_reached"))
        elif status == "ambiguous":
            notes.append(Note(r.id, Method.SUBSET_SUM, "ambiguous_subset_sum"))
        elif status == "unique":
            chosen = [pool[i] for i in idx]
            allocs = tuple(a for c in chosen for a in allocate(c, c.net_amount))
            ev: dict[str, object] = {
                "method": Method.SUBSET_SUM.value,
                "claim_line_ids": [c.id for c in chosen],
            }
            out.append(Match(r.id, Method.SUBSET_SUM, Status.SUGGESTED, allocs, ev, False))
            used = {c.id for c in chosen}
            free = [c for c in free if c.id not in used]
    return out
