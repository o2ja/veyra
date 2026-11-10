"""Volume rebates: tiers, retroactive/incremental, qualifying vs payout base, accrual deltas (§5)."""

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal, localcontext
from itertools import pairwise

from rebate_engine.models import AccrualResult, EngineRuleError, RebateVersion, Tier, VolumeLine
from rebate_engine.money import MONEY_CONTEXT
from rebate_engine.rounding import round_line
from rebate_engine.rules import RulesError, UnsupportedRuleError
from rebate_engine.trace import Trace, Value, trace_hash

ZERO = Decimal(0)
CLAWBACK = "rebate_clawback"


def validate_tiers(tiers: Sequence[Tier]) -> tuple[Tier, ...]:
    """Tiers ordered by lower bound: first 0, strictly increasing, rates in [0, 1] (§3.3)."""
    ordered = tuple(sorted(tiers, key=lambda t: t.lower_bound))
    if not ordered or ordered[0].lower_bound != 0:
        raise EngineRuleError("first tier must start at 0")
    for a, b in pairwise(ordered):
        if b.lower_bound <= a.lower_bound:
            raise EngineRuleError("tier lower bounds must be strictly increasing")
    if any(not 0 <= t.rate <= 1 for t in ordered):
        raise EngineRuleError("tier rates must be in [0, 1]")
    return ordered


def tier_for(tiers: Sequence[Tier], volume: Decimal) -> Tier | None:
    """Highest tier whose (inclusive) lower bound is <= volume; None below the first bound (§5.1)."""
    found = None
    for t in validate_tiers(tiers):
        if t.lower_bound <= volume:
            found = t
    return found


def incremental_raw(tiers: Sequence[Tier], volume: Decimal) -> Decimal:
    """sum rate_i x max(0, min(V, upper_i) - lower_i), upper_i = next lower bound, last unbounded (§5.2)."""
    ordered = validate_tiers(tiers)
    with localcontext(MONEY_CONTEXT):
        total = ZERO
        for i, t in enumerate(ordered):
            upper = ordered[i + 1].lower_bound if i + 1 < len(ordered) else volume
            total += t.rate * max(ZERO, min(volume, upper) - t.lower_bound)
        return total


def is_spa_by_ref(line: VolumeLine) -> bool:
    return bool(line.spa_reference and line.spa_reference.strip())


def _spa_signals(line: VolumeLine) -> tuple[bool, bool]:
    """§5.4 signals (by spa_reference, eligible under an SPA); either one makes it an SPA line."""
    return is_spa_by_ref(line), line.spa_eligible


def calculate_accrual(
    version: RebateVersion,
    lines: Iterable[VolumeLine],
    as_of: date,
    prior_cumulative_accrued: Decimal,
) -> AccrualResult:
    """Cumulative earned amount as of ``as_of`` and the ledger delta against ``prior_cumulative_accrued``.

    ``lines`` are the source lines of ``rules.volume_basis`` (purchases or sales) with signed amounts;
    lines outside the manufacturer, product scope or period window are ignored here.
    """
    rules = version.rules
    tiers = validate_tiers(version.tiers)
    if rules.tier_metric != "amount":  # Release 2 (§3.2); parse_rebate_rules rejects it too
        raise UnsupportedRuleError("tier_metric", rules.tier_metric)
    if rules.tier_mode == "incremental" and rules.spa_lines == "qualify_only":  # §3.3, whatever the volumes
        raise RulesError("tier_mode 'incremental' is incompatible with spa_lines 'qualify_only' (§3.3)")
    window_end = min(rules.period_end, as_of)
    listed = set(version.product_ids)
    t = Trace(
        "agreement",
        {
            "version_id": version.version_id,
            "external_number": version.external_number,
            "as_of": as_of,
            "period": [rules.period_start, rules.period_end],
        },
    )
    with localcontext(MONEY_CONTEXT):
        in_scope = sorted(
            (
                ln
                for ln in lines
                if ln.manufacturer_id == version.manufacturer_id
                and (rules.product_scope_mode == "all" or ln.product_id in listed)
                and rules.period_start <= ln.line_date <= window_end
            ),
            key=lambda ln: (ln.line_date, ln.line_id),
        )
        v_q = v_p = ZERO
        spa_signals: list[Value] = []
        for ln in in_scope:
            # spa_lines policy only applies to the sales basis (§3.2)
            by_ref, eligible = (False, False) if rules.volume_basis == "purchases" else _spa_signals(ln)
            is_spa = by_ref or eligible
            if is_spa:
                spa_signals.append({"line_id": ln.line_id, "spa_reference": by_ref, "spa_eligible": eligible})
            if not (is_spa and rules.spa_lines == "exclude"):
                v_q += ln.amount
            if not (is_spa and rules.spa_lines in ("qualify_only", "exclude")):
                v_p += ln.amount
        t.step(
            "volume",
            basis=rules.volume_basis,
            window=[rules.period_start, window_end],
            lines=len(in_scope),
            spa_lines=rules.spa_lines,
            spa_line_signals=spa_signals,
            qualifying=v_q,
            payout=v_p,
        )

        tier = tier_for(tiers, v_q)
        if rules.tier_mode == "retroactive":
            raw = v_p * tier.rate if tier else ZERO
        else:  # v_p == v_q: qualify_only is refused above, include/exclude keep both bases equal
            raw = incremental_raw(tiers, v_q)
        t.step(
            "tier",
            mode=rules.tier_mode,
            tier_number=tier.tier_number if tier else None,
            lower_bound=tier.lower_bound if tier else None,
            rate=tier.rate if tier else None,
        )
        earned = round_line(raw, rules.rounding)
        t.step("earned", raw=raw, mode=rules.rounding.mode, scale=rules.rounding.scale, result=earned)
        delta = earned - prior_cumulative_accrued
        flags = (CLAWBACK,) if delta < 0 else ()
        t.step("delta", prior_cumulative_accrued=prior_cumulative_accrued, delta=delta)
        trace = t.build(
            rules.schema_version, {"earned_cumulative": earned, "delta": delta, "flags": list(flags)}
        )
        return AccrualResult(
            agreement_version_id=version.version_id,
            period_start=rules.period_start,
            period_end=rules.period_end,
            as_of=as_of,
            qualifying_volume=v_q,
            payout_volume=v_p,
            tier_number=tier.tier_number if tier else None,
            rate=tier.rate if tier else None,
            earned_cumulative=earned,
            prior_cumulative_accrued=prior_cumulative_accrued,
            delta=delta,
            flags=flags,
            trace=trace,
            inputs_hash=trace_hash(trace),
        )
