"""Property tests (TESTING_STRATEGY.md §3.2-§3.3) with an independent reference for tiers and rounding.

The reference works on fractions.Fraction / integer grid units and is written separately from the engine.
"""

from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

from rebate_engine.models import RoundingRule, SalesLine, SpaRunInput, Status, Tier
from rebate_engine.money import quantize
from rebate_engine.rebate import calculate_accrual, incremental_raw, tier_for
from rebate_engine.rounding import allocate_document, round_line
from rebate_engine.spa import calculate_spa
from rebate_engine.trace import canonical_json
from tests.engine_golden.builders import D, item, line, mfr, rebate, version, vline

money = st.decimals(min_value=0, max_value=10**7, places=2, allow_nan=False, allow_infinity=False)
fine = st.decimals(min_value=-1000, max_value=1000, places=6, allow_nan=False, allow_infinity=False)


@st.composite
def tiers(draw: st.DrawFn, nondecreasing: bool = False) -> tuple[Tier, ...]:
    bounds = sorted(draw(st.sets(st.integers(1, 5_000_000), max_size=4)))
    rates = draw(st.lists(st.integers(0, 1000), min_size=len(bounds) + 1, max_size=len(bounds) + 1))
    if nondecreasing:
        rates.sort()
    return tuple(
        Tier(tier_number=i + 1, lower_bound=Decimal(b), rate=Decimal(r) / 10000)
        for i, (b, r) in enumerate(zip([0, *bounds], rates, strict=True))
    )


# ---- reference implementation ----------------------------------------------------------------------


def ref_retro_rate(ts: tuple[Tier, ...], v: Fraction) -> Fraction:
    for t in sorted(ts, key=lambda t: -t.lower_bound):
        if Fraction(t.lower_bound) <= v:
            return Fraction(t.rate)
    return Fraction(0)


def ref_incremental(ts: tuple[Tier, ...], v: Fraction) -> Fraction:
    total, ordered = Fraction(0), sorted(ts, key=lambda t: t.lower_bound)
    for idx in range(len(ordered) - 1, -1, -1):  # walk the bands from the top down
        lower = Fraction(ordered[idx].lower_bound)
        if v > lower:
            top = v if idx == len(ordered) - 1 else min(v, Fraction(ordered[idx + 1].lower_bound))
            total += (top - lower) * Fraction(ordered[idx].rate)
    return total


def ref_half_up_units(x: Fraction, scale: int) -> int:
    """Half-up (away from zero) to an integer number of 10^-scale units."""
    scaled = x * 10**scale
    sign = -1 if scaled < 0 else 1
    return sign * int(abs(scaled) + Fraction(1, 2))


def ref_largest_remainder(raws: list[Fraction], scale: int) -> list[int]:
    units = [r * 10**scale for r in raws]
    floors = [u.numerator // u.denominator for u in units]
    target = ref_half_up_units(sum(raws, Fraction(0)), scale)
    order = sorted(range(len(raws)), key=lambda i: (floors[i] - units[i], i))  # biggest remainder first
    out = list(floors)
    for i in order[: target - sum(floors)]:
        out[i] += 1
    return out


# ---- tiers -----------------------------------------------------------------------------------------


@given(tiers(), money)
def test_retroactive_matches_reference(ts: tuple[Tier, ...], v: Decimal) -> None:
    t = tier_for(ts, v)
    assert Fraction(t.rate if t else 0) == ref_retro_rate(ts, Fraction(v))
    earned = calculate_accrual(rebate(ts), [vline("a", str(v))], date(2026, 12, 31), D(0)).earned_cumulative
    assert earned * 100 == ref_half_up_units(Fraction(v) * ref_retro_rate(ts, Fraction(v)), 2)


@given(tiers(), money)
def test_incremental_matches_reference(ts: tuple[Tier, ...], v: Decimal) -> None:
    assert Fraction(incremental_raw(ts, v)) == ref_incremental(ts, Fraction(v))


@given(tiers(nondecreasing=True), money, money)
def test_retroactive_monotonic_and_not_below_incremental(
    ts: tuple[Tier, ...], a: Decimal, b: Decimal
) -> None:
    lo, hi = sorted((a, b))
    ver = rebate(ts)
    e_lo = calculate_accrual(ver, [vline("x", str(lo))], date(2026, 12, 31), D(0)).earned_cumulative
    e_hi = calculate_accrual(ver, [vline("x", str(hi))], date(2026, 12, 31), D(0)).earned_cumulative
    assert e_lo <= e_hi
    inc = calculate_accrual(
        rebate(ts, tier_mode="incremental"), [vline("x", str(hi))], date(2026, 12, 31), D(0)
    )
    assert inc.earned_cumulative <= e_hi


@given(st.lists(st.tuples(st.integers(0, 300), money.map(lambda d: d - 500000)), min_size=1, max_size=12))
@settings(max_examples=60)
def test_accrual_deltas_sum_to_cumulative_and_rerun_is_zero(events: list[tuple[int, Decimal]]) -> None:
    ver = rebate()
    lines = [
        vline(f"l{i}", str(amt), date(2026, 1, 1) + timedelta(days=d)) for i, (d, amt) in enumerate(events)
    ]
    ledger, last = D(0), None
    for as_of in sorted({date(2026, 1, 1) + timedelta(days=d) for d, _ in events}):
        r = calculate_accrual(ver, lines, as_of, ledger)
        ledger += r.delta
        last = r
        again = calculate_accrual(ver, list(reversed(lines)), as_of, ledger)
        assert again.delta == 0 and canonical_json(again.trace["result"]) != ""
    assert last is not None and ledger == last.earned_cumulative


# ---- rounding --------------------------------------------------------------------------------------


@given(st.lists(fine.map(abs), min_size=1, max_size=15), st.integers(0, 4))
def test_largest_remainder_matches_reference(raws: list[Decimal], scale: int) -> None:
    rule = RoundingRule(mode="half_up", scale=scale, timing="per_document")
    got = allocate_document(raws, rule)
    assert [int(g.scaleb(scale)) for g in got] == ref_largest_remainder([Fraction(r) for r in raws], scale)
    assert sum(got, D(0)) == quantize(sum(raws, D(0)), scale, "half_up")


@given(
    st.lists(fine, min_size=1, max_size=15),
    st.integers(0, 4),
    st.sampled_from(["half_up", "half_even", "down"]),
)
def test_rounding_bounds(raws: list[Decimal], scale: int, mode: str) -> None:
    unit = D(1).scaleb(-scale)
    rule = RoundingRule(mode=mode, scale=scale, timing="per_document")
    got = allocate_document(raws, rule)
    assert sum(got, D(0)) == quantize(sum(raws, D(0)), scale, mode)
    assert all(abs(g - r) < unit for g, r in zip(got, raws, strict=True))
    for r in raws:
        bound = unit if mode == "down" else unit / 2
        assert abs(round_line(r, rule) - r) <= bound


# ---- SPA run: order independence, idempotence ------------------------------------------------------

POOL = [
    line(
        line_id=f"L{i}",
        document_number=f"INV-{i % 4}",
        line_number=i,
        invoice_date=date(2026, 6, 1 + i % 5),
        quantity=D(5 + 7 * i),
    )
    for i in range(10)
]


@given(st.permutations(POOL), st.sampled_from([None, D(40), D(120)]))
@settings(max_examples=40)
def test_spa_results_independent_of_input_order_and_byte_identical(
    lines: list[SalesLine], cap: Decimal | None
) -> None:
    v = version(items=(item(max_quantity=cap),))

    def go(ls: list[SalesLine]) -> dict[str, tuple[Status, Decimal, Decimal | None, str, str]]:
        res = calculate_spa(
            SpaRunInput(run_date=date(2026, 7, 10), lines=tuple(ls), versions=(v,), manufacturers=(mfr(),))
        )
        return {
            r.line_id: (r.status, r.claim_amount, r.eligible_quantity, r.inputs_hash, canonical_json(r.trace))
            for r in res
        }

    a, b = go(lines), go(POOL)
    assert a == b
    total = sum((x[1] for x in a.values()), D(0))
    if cap is not None:
        assert total <= cap * D("22.00")
