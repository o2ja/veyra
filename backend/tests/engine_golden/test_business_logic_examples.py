"""Golden catalogue, BUSINESS_LOGIC.md §14 (and the other numbered examples). Expected values are typed
from the document, never taken from engine output. GL-V1/GL-V2 are reconciliation (rebate_matching, S8)."""

from datetime import date
from typing import Any

from rebate_engine.deadlines import deadline, reminder_dates
from rebate_engine.models import (
    Code,
    LineResult,
    PriorClaim,
    ReturnOriginal,
    RoundingRule,
    SpaRunInput,
    Status,
    UomConversion,
    VolumeLine,
)
from rebate_engine.rebate import CLAWBACK, calculate_accrual
from rebate_engine.spa import PARTIALLY_ELIGIBLE, calculate_spa, propose_recalculation
from tests.engine_golden.builders import (
    HALF_UP_2,
    D,
    item,
    line,
    mfr,
    rebate,
    spa_rules,
    step,
    version,
    vline,
)

RUN = date(2026, 7, 10)


def run(lines: Any, versions: Any = None, run_date: date = RUN, **kw: Any) -> list[LineResult]:
    return list(
        calculate_spa(
            SpaRunInput(
                run_date=run_date,
                lines=tuple(lines),
                versions=tuple(versions or (version(),)),
                manufacturers=kw.pop("manufacturers", (mfr(),)),
                **kw,
            )
        )
    )


def steps(r: LineResult) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in r.trace["steps"]:  # type: ignore[union-attr]
        assert isinstance(s, dict)
        out[str(s["step"])] = s
    return out


# ---- SPA §4 ----------------------------------------------------------------------------------------


def test_gl_s1_price_difference() -> None:
    (r,) = run([line()])
    assert (r.status, r.claim_amount, r.claim_unit_amount, r.eligible_quantity) == (
        Status.ELIGIBLE,
        D("2200.00"),
        D("22.00"),
        D(100),
    )
    s = steps(r)
    assert s["calc"]["basis"] == "price_difference"
    assert (s["calc"]["reference_cost"], s["calc"]["deviated_cost"], s["calc"]["unit_amount"]) == (
        "100.00",
        "78.00",
        "22.00",
    )
    assert s["round"] == {
        "step": "round",
        "mode": "half_up",
        "scale": 2,
        "timing": "per_line",
        "result": "2200.00",
    }
    assert r.trace["engine_version"] == "1.0.0" and r.trace["rules_schema_version"] == 1
    assert list(s)[:3] == ["candidate_versions", "customer_scope", "product_scope"]


def test_gl_s3_guaranteed_margin() -> None:
    v = version(rules=spa_rules(basis="guaranteed_margin"), items=(item(rate=D("0.12"), deviated_cost=None),))
    (r,) = run([line()], [v])
    assert (r.claim_unit_amount, r.claim_amount) == (D("22.56"), D("2256.00"))


def test_gl_s4_percent_of_sell() -> None:
    v = version(rules=spa_rules(basis="percent_of_sell"), items=(item(rate=D("0.05")),))
    (r,) = run([line()], [v])
    assert (r.claim_unit_amount, r.claim_amount) == (D("4.40"), D("440.00"))


def test_percent_of_cost() -> None:
    # §4.1: u = C_ref x rate = 100.00 x 0.05 = 5.00; 100 x 5.00 = 500.00 (hand-derived)
    v = version(rules=spa_rules(basis="percent_of_cost"), items=(item(rate=D("0.05")),))
    (r,) = run([line()], [v])
    assert (r.claim_unit_amount, r.claim_amount) == (D("5.00"), D("500.00"))


def test_gl_s5_flat_per_foot_with_reel_conversion() -> None:
    v = version(
        rules=spa_rules(basis="flat_per_unit", claim_uom="FT", price_check_mode="ignore"),
        items=(item(flat_amount=D("0.04"), claim_uom="FT", sell_price=None),),
    )
    conv = (UomConversion(product_id="P1", from_uom="RL", to_uom="FT", factor=D(500)),)
    (r,) = run([line(quantity=D(3), uom="RL", base_uom="FT")], [v], conversions=conv)
    assert (r.eligible_quantity, r.claim_amount, r.claim_uom) == (D(1500), D("60.00"), "FT")
    assert steps(r)["uom"] == {"step": "uom", "line_uom": "RL", "claim_uom": "FT", "factor": "500"}


def test_gl_s6_cap_partially_exhausted() -> None:
    v = version(items=(item(max_quantity=D(100)),))
    a = line(line_id="A", document_number="INV-1", invoice_date=date(2026, 6, 1), quantity=D(60))
    b = line(line_id="B", document_number="INV-2", invoice_date=date(2026, 6, 2), quantity=D(60))
    c = line(line_id="C", document_number="INV-3", invoice_date=date(2026, 6, 3), quantity=D(5))
    ra, rb, rc = run([c, b, a], [v])  # input order must not matter (§1.5)
    assert (ra.line_id, ra.claim_amount, ra.flags) == ("A", D("1320.00"), ())
    assert (rb.line_id, rb.claim_amount, rb.eligible_quantity, rb.excess_quantity) == (
        "B",
        D("880.00"),
        D(40),
        D(20),
    )
    assert PARTIALLY_ELIGIBLE in rb.flags
    assert (rc.status, rc.exception_code) == (Status.EXCEPTION, Code.CAP_EXHAUSTED)


def test_gl_s7_rounding_per_line_vs_per_document() -> None:
    def three(timing: str) -> list[LineResult]:
        rules = spa_rules(
            basis="flat_per_unit",
            price_check_mode="ignore",
            rounding=RoundingRule(mode="half_up", scale=2, timing=timing),
        )
        v = version(rules=rules, items=(item(flat_amount=D("0.335")),))
        lines = [line(line_id=f"L{i}", line_number=i, quantity=D(1)) for i in (1, 2, 3)]
        return run(lines, [v])

    per_line = three("per_line")
    assert [r.claim_amount for r in per_line] == [D("0.34")] * 3
    assert sum(r.claim_amount for r in per_line) == D("1.02")
    per_doc = three("per_document")
    assert [r.claim_amount for r in per_doc] == [D("0.34"), D("0.34"), D("0.33")]
    assert sum(r.claim_amount for r in per_doc) == D("1.01")
    assert steps(per_doc[0])["round"]["document_total"] == "1.01"


def _prior(unit: str, qty: int) -> PriorClaim:
    return PriorClaim(
        agreement_id="A1",
        agreement_version_id="V1",
        claim_uom="EA",
        quantity=D(qty),
        unit_amount=D(unit),
        claim_amount=D(unit) * qty,
        rounding=HALF_UP_2,
    )


def test_gl_s8_return_after_claim() -> None:
    prior = _prior("22.00", 100)
    orig = line(line_id="O", returned_quantity=D(15), prior_claim=prior)
    ret = line(
        line_id="R",
        document_number="CN-1",
        invoice_date=date(2026, 7, 25),
        quantity=D(-15),
        return_original=ReturnOriginal(
            line_id="O", quantity=D(100), returned_quantity=D(15), prior_claim=prior
        ),
    )
    ro, rr = run([orig, ret], manufacturers=(mfr(allow_negative_claims=True),))
    assert (ro.status, ro.claim_amount, ro.eligible_quantity) == (
        Status.ELIGIBLE,
        D("2200.00"),
        D(100),
    )  # frozen
    assert (rr.status, rr.claim_amount) == (Status.ADJUSTMENT, D("-330.00"))


def test_gl_s8_return_restores_cap_consumption() -> None:
    # §7 S8: "Quantity-cap consumption is reduced by 15" -> cap 100, 100 claimed, 15 returned: 15 remain
    v = version(items=(item(max_quantity=D(100)),))
    orig = line(line_id="O", returned_quantity=D(15), prior_claim=_prior("22.00", 100))
    later = line(line_id="N", document_number="INV-9", invoice_date=date(2026, 6, 20), quantity=D(20))
    _, rn = run([orig, later], [v])
    assert (rn.eligible_quantity, rn.excess_quantity, rn.claim_amount) == (D(15), D(5), D("330.00"))


def test_gl_s9_return_before_claim() -> None:
    orig = line(line_id="O", returned_quantity=D(15))
    ret = line(
        line_id="R",
        document_number="CN-1",
        invoice_date=date(2026, 6, 20),
        quantity=D(-15),
        return_original=ReturnOriginal(line_id="O", quantity=D(100), returned_quantity=D(15)),
    )
    ro, rr = run([orig, ret])
    assert (ro.status, ro.eligible_quantity, ro.claim_amount) == (Status.ELIGIBLE, D(85), D("1870.00"))
    assert steps(ro)["returns"]["net"] == "85"
    assert (rr.status, rr.claim_amount) == (Status.NETTED, D(0))


def test_gl_s10_date_basis_ship_vs_invoice() -> None:
    ln = line(invoice_date=date(2026, 6, 28), ship_date=date(2026, 7, 2))
    end = date(2026, 6, 30)
    (ship,) = run([ln], [version(rules=spa_rules(date_basis="ship_date"), valid_to=end)])
    (inv,) = run([ln], [version(rules=spa_rules(date_basis="invoice_date"), valid_to=end)])
    assert (ship.status, ship.exception_code) == (Status.EXCEPTION, Code.EXPIRED_AGREEMENT)
    assert (inv.status, inv.claim_amount) == (Status.ELIGIBLE, D("2200.00"))
    assert steps(ship)["date_check"]["value"] == "2026-07-02" and steps(ship)["date_check"]["pass"] is False


def test_gl_s11_price_mismatch() -> None:
    ln = line(unit_price=D("90.00"))
    (flag,) = run([ln])
    assert (flag.status, flag.exception_code, flag.claim_amount) == (
        Status.EXCEPTION,
        Code.PRICE_MISMATCH,
        D(0),
    )
    assert steps(flag)["price_check"]["difference"] == "2.00"
    (ignore,) = run([ln], [version(rules=spa_rules(price_check_mode="ignore"))])
    assert (ignore.status, ignore.claim_amount) == (Status.ELIGIBLE, D("2200.00"))


def test_gl_s12_conflicts() -> None:
    v1 = version(version_id="V1", agreement_id="A1", external_number="SPA-1")
    v2 = version(
        version_id="V2", agreement_id="A2", external_number="SPA-2", items=(item(deviated_cost=D("80.00")),)
    )
    ln = line(spa_reference=None)
    (equal,) = run([ln], [v1, v2])
    assert (equal.exception_code, equal.claim_amount) == (Code.CONFLICTING_AGREEMENTS, D(0))
    (still_equal,) = run([ln], [v1, v2], manufacturers=(mfr(conflict_policy="priority"),))
    assert still_equal.exception_code is Code.CONFLICTING_AGREEMENTS  # equal top priority
    v2p = version(
        version_id="V2",
        agreement_id="A2",
        external_number="SPA-2",
        priority=10,
        items=(item(deviated_cost=D("80.00")),),
    )
    (win,) = run([ln], [v1, v2p], manufacturers=(mfr(conflict_policy="priority"),))
    assert (win.status, win.agreement_version_id, win.claim_amount) == (Status.ELIGIBLE, "V2", D("2000.00"))
    (flagged,) = run([ln], [v1, v2p])  # conflict_policy = flag ignores priority
    assert flagged.exception_code is Code.CONFLICTING_AGREEMENTS


# ---- Deadlines §12 ---------------------------------------------------------------------------------


def test_gl_d1_claim_deadline() -> None:
    assert deadline(date(2026, 3, 1), 60) == date(2026, 4, 30)
    ln = line(invoice_date=date(2026, 3, 1), ship_date=date(2026, 3, 1))
    (late,) = run([ln], run_date=date(2026, 5, 2))
    assert (late.status, late.exception_code, late.claim_amount) == (
        Status.EXCEPTION,
        Code.OUTSIDE_CLAIM_WINDOW,
        D("2200.00"),
    )
    (last_day,) = run([ln], run_date=date(2026, 4, 30))  # inclusive
    assert last_day.status is Status.ELIGIBLE


def test_gl_d3_claim_deadline_month_end() -> None:
    # §12 Example D3: invoice 2026-03-10, period end 2026-03-31, + 60 days = 2026-05-30 (inclusive)
    ln = line(invoice_date=date(2026, 3, 10), ship_date=date(2026, 3, 10))
    pe = (mfr(claim_window_basis="month_end", claim_window_days=60),)
    (last_day,) = run([ln], run_date=date(2026, 5, 30), manufacturers=pe)
    assert last_day.status is Status.ELIGIBLE
    cw = steps(last_day)["claim_window"]
    assert (cw["basis"], cw["month_end"], cw["deadline"]) == ("month_end", "2026-03-31", "2026-05-30")
    (late,) = run([ln], run_date=date(2026, 5, 31), manufacturers=pe)
    assert (late.status, late.exception_code, late.claim_amount) == (
        Status.EXCEPTION,
        Code.OUTSIDE_CLAIM_WINDOW,
        D("2200.00"),
    )


def test_gl_d2_dispute_deadline_and_reminders() -> None:
    due = deadline(date(2026, 5, 15), 60)
    assert due == date(2026, 7, 14)
    assert reminder_dates(due, (14, 7, 3)) == (date(2026, 6, 30), date(2026, 7, 7), date(2026, 7, 11))


# ---- Rebates §5 ------------------------------------------------------------------------------------


def test_gl_r1_retroactive_catch_up() -> None:
    v = rebate()
    at_990k = calculate_accrual(v, [vline("a", "990000")], date(2026, 8, 30), D(0))
    assert (at_990k.tier_number, at_990k.earned_cumulative, at_990k.delta) == (
        1,
        D("14850.00"),
        D("14850.00"),
    )
    # "Purchases reach exactly 1,000,000 -> earned 25,000.00 -> delta = 25,000.00 - 14,850.00 = 10,150.00"
    at_1m = calculate_accrual(
        v, [vline("a", "990000"), vline("b", "10000")], date(2026, 8, 31), at_990k.earned_cumulative
    )
    assert (at_1m.tier_number, at_1m.earned_cumulative, at_1m.delta) == (2, D("25000.00"), D("10150.00"))
    # breakdown: 9,900.00 catch-up on the 990,000 (x (2.5% - 1.5%)) + 250.00 on the new 10,000 (x 2.5%)
    assert at_1m.delta == D("9900.00") + D("250.00")
    # 990,000 -> 1,010,000 with 14,850.00 accrued: 25,250.00 - 14,850.00 = 10,400.00
    at_101 = calculate_accrual(
        v, [vline("a", "990000"), vline("b", "20000")], date(2026, 8, 31), D("14850.00")
    )
    assert (at_101.earned_cumulative, at_101.delta) == (D("25250.00"), D("10400.00"))


def test_gl_r2_clawback() -> None:
    lines = [vline("a", "1010000"), vline("credit", "-20000", date(2026, 9, 15))]
    r = calculate_accrual(rebate(), lines, date(2026, 9, 30), D("25250.00"))
    assert (r.qualifying_volume, r.tier_number, r.earned_cumulative, r.delta) == (
        D(990000),
        1,
        D("14850.00"),
        D("-10400.00"),
    )
    assert CLAWBACK in r.flags


def test_gl_r3_qualify_only_exclude_boundary() -> None:
    sales = [vline("plain", "1000000"), vline("spa", "200000", spa_reference="SPA-9")]
    q = calculate_accrual(
        rebate(volume_basis="sales", spa_lines="qualify_only"), sales, date(2026, 12, 31), D(0)
    )
    assert (q.qualifying_volume, q.payout_volume, q.tier_number, q.earned_cumulative) == (
        D(1200000),
        D(1000000),
        2,
        D("25000.00"),
    )
    ex = calculate_accrual(rebate(volume_basis="sales", spa_lines="exclude"), sales, date(2026, 12, 31), D(0))
    assert (ex.qualifying_volume, ex.tier_number, ex.earned_cumulative) == (D(1000000), 2, D("25000.00"))
    edge = [vline("plain", "999999.00"), vline("spa", "200000", spa_reference="SPA-9")]
    b = calculate_accrual(rebate(volume_basis="sales", spa_lines="exclude"), edge, date(2026, 12, 31), D(0))
    assert (b.tier_number, b.earned_cumulative) == (1, D("14999.99"))
    assert b.trace["steps"][1]["step"] == "tier"  # type: ignore[index,call-overload]


def _d2_lines() -> list[VolumeLine]:
    return [
        vline("plain", "1000000"),
        vline("late", "100000", spa_exception_signal=True),  # SPA result outside_claim_window
        vline("capped", "50000", spa_exception_signal=True),  # SPA result cap_exhausted
        vline("mismatch", "20000"),  # SPA result price_mismatch: no signal, NOT an SPA line
    ]


def test_gl_r5_d2_exception_signals_include_exclude_qualify_only() -> None:
    """BUSINESS_LOGIC 5.4 (owner decision D2). Tiers 0 -> 1.5%, 1,000,000 -> 2.5%, retroactive, sales basis.
    SPA lines: late 100,000 + capped 50,000 = 150,000. Non-SPA: plain 1,000,000 + mismatch 20,000 = 1,020,000.
    include: V_q = V_p = 1,170,000 -> tier 2 -> 1,170,000 x 0.025 = 29,250.00
    exclude: V_q = V_p = 1,020,000 -> tier 2 -> 1,020,000 x 0.025 = 25,500.00
    qualify_only: V_q = 1,170,000 -> tier 2; V_p = 1,020,000 -> 1,020,000 x 0.025 = 25,500.00"""
    asof = date(2026, 12, 31)
    inc = calculate_accrual(rebate(volume_basis="sales", spa_lines="include"), _d2_lines(), asof, D(0))
    assert (inc.qualifying_volume, inc.payout_volume, inc.earned_cumulative) == (
        D(1170000),
        D(1170000),
        D("29250.00"),
    )
    ex = calculate_accrual(rebate(volume_basis="sales", spa_lines="exclude"), _d2_lines(), asof, D(0))
    assert (ex.qualifying_volume, ex.payout_volume, ex.earned_cumulative) == (
        D(1020000),
        D(1020000),
        D("25500.00"),
    )
    qo = calculate_accrual(rebate(volume_basis="sales", spa_lines="qualify_only"), _d2_lines(), asof, D(0))
    assert (qo.qualifying_volume, qo.payout_volume, qo.tier_number, qo.earned_cumulative) == (
        D(1170000),
        D(1020000),
        2,
        D("25500.00"),
    )
    signals = {s["line_id"]: s for s in step(ex, "volume")["spa_line_signals"]}
    assert sorted(signals) == ["capped", "late"]  # price_mismatch line is not recorded as an SPA line
    assert signals["late"] == {
        "line_id": "late",
        "spa_reference": False,
        "spa_eligible": False,
        "spa_exception_signal": True,
    }


def test_gl_r5_d2_exception_signal_moves_the_tier() -> None:
    """Boundary: plain 900,000 + late 100,000 (outside_claim_window). exclude -> V = 900,000 -> tier 1 ->
    900,000 x 0.015 = 13,500.00; qualify_only -> V_q = 1,000,000 (inclusive bound) -> tier 2, V_p = 900,000 ->
    900,000 x 0.025 = 22,500.00; a price_mismatch line (no signal) in place of late is plain volume: exclude -> V = 1,000,000 -> tier 2 ->
    1,000,000 x 0.025 = 25,000.00."""
    asof = date(2026, 12, 31)
    lines = [vline("plain", "900000"), vline("late", "100000", spa_exception_signal=True)]
    ex = calculate_accrual(rebate(volume_basis="sales", spa_lines="exclude"), lines, asof, D(0))
    assert (ex.tier_number, ex.earned_cumulative) == (1, D("13500.00"))
    qo = calculate_accrual(rebate(volume_basis="sales", spa_lines="qualify_only"), lines, asof, D(0))
    assert (qo.tier_number, qo.earned_cumulative) == (2, D("22500.00"))
    other = [vline("plain", "900000"), vline("mismatch", "100000")]
    ex2 = calculate_accrual(rebate(volume_basis="sales", spa_lines="exclude"), other, asof, D(0))
    assert (ex2.tier_number, ex2.earned_cumulative) == (2, D("25000.00"))


def test_gl_r4_incremental_vs_retroactive() -> None:
    lines = [vline("a", "1500000")]
    inc = calculate_accrual(rebate(tier_mode="incremental"), lines, date(2026, 12, 31), D(0))
    retro = calculate_accrual(rebate(), lines, date(2026, 12, 31), D(0))
    assert (inc.earned_cumulative, retro.earned_cumulative) == (D("27500.00"), D("37500.00"))


# ---- Recalculation §9.3 ----------------------------------------------------------------------------


def test_gl_a1_amendment_recalculation() -> None:
    """v1 pays 22.00/unit; amendment from Jul 1 reduces to 20.00; July lines claimed at 22.00 (q=500)."""
    v1a = version(version_id="V1a", valid_to=date(2026, 6, 30))
    v2 = version(version_id="V2", valid_from=date(2026, 7, 1), items=(item(deviated_cost=D("80.00")),))
    june = line(line_id="J", invoice_date=date(2026, 6, 15), ship_date=date(2026, 6, 15), quantity=D(500))
    july = line(
        line_id="K",
        invoice_date=date(2026, 7, 15),
        ship_date=date(2026, 7, 15),
        quantity=D(500),
        prior_claim=_prior("22.00", 500),
    )
    rj, rk = run([june, july], [v1a, v2], run_date=date(2026, 8, 1))
    assert (rj.agreement_version_id, rj.claim_amount) == ("V1a", D("11000.00"))  # unchanged at 22.00
    assert propose_recalculation(D("11000.00"), rj) is None
    assert (rk.agreement_version_id, rk.claim_unit_amount) == ("V2", D("20.00"))
    assert propose_recalculation(D("11000.00"), rk) == D("-1000.00")


def test_recalculation_exception_proposes_full_reversal() -> None:
    (r,) = run([line(unit_price=D("95.00"))])
    assert propose_recalculation(D("2200.00"), r) == D("-2200.00")
