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
)
from rebate_engine.rebate import CLAWBACK, calculate_accrual
from rebate_engine.spa import PARTIALLY_ELIGIBLE, calculate_spa, propose_recalculation
from tests.engine_golden.builders import HALF_UP_2, D, item, line, mfr, rebate, spa_rules, version, vline

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
