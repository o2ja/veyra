"""Every SPA gate and exception code (BUSINESS_LOGIC.md §4.0, §7, §8); values hand-derived."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from rebate_engine import adapters
from rebate_engine.adapters import AdapterInput, UnknownAdapter
from rebate_engine.models import (
    Code,
    CustomerScope,
    EngineRuleError,
    LineResult,
    ManufacturerSettings,
    ReturnOriginal,
    RoundingRule,
    SalesLine,
    SpaRunInput,
    SpaVersion,
    Status,
    UomConversion,
)
from rebate_engine.rules import UnsupportedRuleError
from rebate_engine.spa import calculate_spa
from tests.engine_golden.builders import D, item, line, mfr, spa_rules, step, version, warnings

RUN = date(2026, 7, 10)


def one(ln: SalesLine | None = None, versions: Sequence[SpaVersion] | None = None, **kw: Any) -> LineResult:
    (r,) = calculate_spa(
        SpaRunInput(
            run_date=kw.pop("run_date", RUN),
            lines=(ln or line(),),
            versions=tuple(versions if versions is not None else (version(),)),
            manufacturers=kw.pop("manufacturers", (mfr(),)),
            **kw,
        )
    )
    return r


@pytest.mark.parametrize(
    ("ln", "versions", "code"),
    [
        (line(product_id=None, manufacturer_id=None), None, Code.UNKNOWN_PRODUCT),
        (line(manufacturer_id="M2"), None, Code.NO_AGREEMENT),
        (line(customer_id="C9"), None, Code.INELIGIBLE_CUSTOMER),
        (line(product_id="P9"), None, Code.INELIGIBLE_PRODUCT),
        (line(invoice_date=date(2025, 12, 31)), None, Code.AGREEMENT_NOT_STARTED),
        (line(invoice_date=date(2027, 1, 1)), None, Code.EXPIRED_AGREEMENT),
        (
            line(spa_reference=None),
            [version(rules=spa_rules(spa_reference_required=True))],
            Code.MISSING_SPA_REFERENCE,
        ),
        (
            line(spa_reference="SPA-2"),
            [version(rules=spa_rules(spa_reference_required=True))],
            Code.SPA_REFERENCE_MISMATCH,
        ),
        (line(), [version(items=(item(reference_cost=None),))], Code.MISSING_COST),
        (line(), [version(items=(item(deviated_cost=None),))], Code.MISSING_COST),
        (line(), [version(rules=spa_rules(cost_source="line_cost"))], Code.MISSING_COST),
        (line(), [version(rules=spa_rules(cost_source="replacement_cost"))], Code.MISSING_REPLACEMENT_COST),
        (line(uom="CT"), None, Code.MISSING_UOM_CONVERSION),
        (line(), [version(items=(item(deviated_cost=D("100.00")),))], Code.NO_CLAIM_DUE),
        (line(), [version(items=(item(deviated_cost=D("101.00")),))], Code.NEGATIVE_CLAIM_AMOUNT),
        (line(invoice_date=date(2026, 5, 1)), None, Code.OUTSIDE_CLAIM_WINDOW),
    ],
    ids=lambda v: v.value if isinstance(v, Code) else "",
)
def test_gate_codes(ln: SalesLine, versions: list[SpaVersion] | None, code: Code) -> None:
    r = one(ln, versions)
    assert r.exception_code is code
    expected_status = Status.NOT_APPLICABLE if code is Code.NO_CLAIM_DUE else Status.EXCEPTION
    assert r.status is expected_status
    if code is not Code.OUTSIDE_CLAIM_WINDOW:
        assert r.claim_amount == 0


def test_no_versions_at_all_is_no_agreement() -> None:
    assert one(line(), []).exception_code is Code.NO_AGREEMENT


def test_spa_reference_compare_is_case_and_space_insensitive() -> None:
    v = version(rules=spa_rules(spa_reference_required=True))
    assert one(line(spa_reference=" spa - 1 "), [v]).status is Status.ELIGIBLE


def test_non_required_reference_mismatch_is_only_a_warning() -> None:
    r = one(line(spa_reference="OTHER"))
    assert r.status is Status.ELIGIBLE and "spa_reference_differs" in warnings(r)


def test_ship_date_basis_falls_back_to_invoice_date_and_traces_it() -> None:
    r = one(line(ship_date=None), [version(rules=spa_rules(date_basis="ship_date"))])
    assert r.status is Status.ELIGIBLE
    assert step(r, "date_check")["fallback"] == "invoice_date"


def test_customer_scope_inheritance_and_exclusion() -> None:
    parent_listed = version(customers=(CustomerScope(customer_id="PARENT", scope="include"),))
    assert (
        one(line(customer_id="CHILD", customer_ancestor_ids=("PARENT",)), [parent_listed]).status
        is Status.ELIGIBLE
    )
    child_excluded = version(
        customers=(
            CustomerScope(customer_id="PARENT", scope="include"),
            CustomerScope(customer_id="CHILD", scope="exclude"),
        )
    )
    r = one(line(customer_id="CHILD", customer_ancestor_ids=("PARENT",)), [child_excluded])
    assert r.exception_code is Code.INELIGIBLE_CUSTOMER
    all_mode = version(rules=spa_rules(customer_scope_mode="all"), customers=())
    assert one(line(customer_id="ANY"), [all_mode]).status is Status.ELIGIBLE


def test_line_cost_basis_converts_cost_per_claim_uom() -> None:
    # guaranteed margin with line cost, 2 BX of 10 EA: C_ref = 380/10 = 38, P = 330/10 = 33,
    # u = 38 - 33 x 0.88 = 8.96, q = 20 EA -> 179.20 (SC-03 arithmetic per unit, hand-derived)
    v = version(
        rules=spa_rules(basis="guaranteed_margin", cost_source="line_cost"),
        items=(item(rate=D("0.12"), sell_price=None),),
    )
    conv = (UomConversion(product_id=None, from_uom="BX", to_uom="EA", factor=D(10)),)
    r = one(
        line(uom="BX", quantity=D(2), unit_price=D("330.00"), unit_cost=D("380.00")), [v], conversions=conv
    )
    assert (r.claim_unit_amount, r.eligible_quantity, r.claim_amount) == (D("8.96"), D(20), D("179.20"))


def test_replacement_cost_source() -> None:
    v = version(rules=spa_rules(cost_source="replacement_cost"))
    r = one(line(replacement_cost=D("95.00")), [v])
    assert (r.claim_unit_amount, r.claim_amount) == (D("17.00"), D("1700.00"))  # (95 - 78) x 100


def test_uom_lookup_order_and_inverse() -> None:
    from rebate_engine.uom import find_factor

    convs = (
        UomConversion(product_id=None, from_uom="RL", to_uom="FT", factor=D(100)),
        UomConversion(product_id="P1", from_uom="FT", to_uom="RL", factor=D("0.002")),
    )
    assert find_factor(convs, "P1", "RL", "FT") == D(500)  # product-specific inverse beats tenant direct
    assert find_factor(convs, "P2", "RL", "FT") == D(100)  # tenant default
    assert find_factor(convs, "P2", "FT", "RL") == D("0.01")  # tenant inverse
    assert find_factor(convs, "P2", "CT", "FT") is None
    assert find_factor((), None, "EA", "EA") == 1


def test_price_check_in_claim_uom_when_uoms_differ() -> None:
    # sell_price 0.60 per FT; line 285.00 per RL of 500 FT = 0.57 per FT -> mismatch (0.03 > 0.005)
    v = version(
        rules=spa_rules(basis="flat_per_unit", claim_uom="FT"),
        items=(item(flat_amount=D("0.04"), sell_price=D("0.60")),),
    )
    conv = (UomConversion(product_id="P1", from_uom="RL", to_uom="FT", factor=D(500)),)
    r = one(line(uom="RL", quantity=D(3), unit_price=D("285.00")), [v], conversions=conv)
    assert r.exception_code is Code.PRICE_MISMATCH
    ok = one(line(uom="RL", quantity=D(3), unit_price=D("300.00")), [v], conversions=conv)
    assert ok.claim_amount == D("60.00")


def test_price_within_tolerance_passes() -> None:
    assert one(line(unit_price=D("88.005"))).status is Status.ELIGIBLE
    assert one(line(unit_price=D("88.006"))).exception_code is Code.PRICE_MISMATCH


def test_cap_scope_none_ignores_max_quantity() -> None:
    v = version(rules=spa_rules(cap_scope="none"), items=(item(max_quantity=D(10)),))
    assert one(line(), [v]).eligible_quantity == D(100)


def test_prior_consumption_from_frozen_batches_counts() -> None:
    v = version(items=(item(max_quantity=D(100)),))
    r = one(line(), [v], prior_consumption={("A1", "P1"): D(70)})
    assert (r.eligible_quantity, r.excess_quantity, r.claim_amount) == (D(30), D(70), D("660.00"))
    assert one(line(), [v], prior_consumption={("A1", "P1"): D(100)}).exception_code is Code.CAP_EXHAUSTED


def test_fully_returned_unclaimed_original_has_no_claim_due() -> None:
    r = one(line(returned_quantity=D(100)))
    assert (r.status, r.exception_code) == (Status.NOT_APPLICABLE, Code.NO_CLAIM_DUE)


def test_return_without_original_and_exceeding_original() -> None:
    assert one(line(quantity=D(-5))).exception_code is Code.RETURN_ORIGINAL_MISSING
    over = ReturnOriginal(line_id="O", quantity=D(10), returned_quantity=D(12))
    assert one(line(quantity=D(-12), return_original=over)).exception_code is Code.RETURN_EXCEEDS_ORIGINAL


def test_claim_window_skipped_with_warning_when_settings_missing() -> None:
    r = one(line(invoice_date=date(2026, 1, 5)), manufacturers=())
    assert r.status is Status.ELIGIBLE and "claim_window_settings_missing" in warnings(r)


def test_claim_window_ship_date_basis() -> None:
    ln = line(invoice_date=date(2026, 5, 1), ship_date=date(2026, 5, 20))
    ship = (mfr(claim_window_basis="ship_date"),)
    assert one(ln, manufacturers=ship).status is Status.ELIGIBLE  # 05-20 + 60 = 07-19 >= 07-10


def test_claim_window_month_end_leap_february() -> None:
    # 2028-02-10 -> period end 2028-02-29 (leap year) -> + 60 days = 2028-04-29
    v = version(valid_from=date(2028, 1, 1), valid_to=date(2028, 12, 31))
    ln = line(invoice_date=date(2028, 2, 10), ship_date=date(2028, 2, 10))
    pe = (mfr(claim_window_basis="month_end"),)
    r = one(ln, [v], manufacturers=pe, run_date=date(2028, 4, 29))
    assert r.status is Status.ELIGIBLE
    cw = step(r, "claim_window")
    assert (cw["basis"], cw["month_end"], cw["deadline"]) == ("month_end", "2028-02-29", "2028-04-29")
    late = one(ln, [v], manufacturers=pe, run_date=date(2028, 4, 30))
    assert late.exception_code is Code.OUTSIDE_CLAIM_WINDOW


def test_claim_window_month_end_uses_date_validity_basis() -> None:
    # date_basis ship_date: claim date 2026-04-02 (ship), not 2026-03-30 (invoice) -> period end 2026-04-30
    v = version(rules=spa_rules(date_basis="ship_date"))
    ln = line(invoice_date=date(2026, 3, 30), ship_date=date(2026, 4, 2))
    r = one(ln, [v], manufacturers=(mfr(claim_window_basis="month_end"),), run_date=date(2026, 6, 29))
    assert r.status is Status.ELIGIBLE  # 04-30 + 60 = 06-29
    assert step(r, "claim_window")["month_end"] == "2026-04-30"
    # missing ship date -> invoice date fallback (2026-03-30 -> period end 2026-03-31), warned
    fb = one(
        replace(ln, ship_date=None),
        [v],
        manufacturers=(mfr(claim_window_basis="month_end"),),
        run_date=date(2026, 6, 29),
    )
    assert fb.exception_code is Code.OUTSIDE_CLAIM_WINDOW  # 03-31 + 60 = 05-30 < 06-29
    assert step(fb, "claim_window")["month_end"] == "2026-03-31"
    assert "ship_date_fallback" in warnings(fb)


@pytest.mark.parametrize("max_quantity", [D(10), None])
def test_per_agreement_cap_is_refused_regardless_of_max_quantity(max_quantity: Decimal | None) -> None:
    v = version(rules=spa_rules(cap_scope="per_agreement"), items=(item(max_quantity=max_quantity),))
    with pytest.raises(UnsupportedRuleError, match="Release 2") as exc:
        one(line(), [v])
    assert exc.value.code == "not_supported_until_release_2"


def test_rounding_modes() -> None:
    # flat 0.125 x 1: half_up 0.13, half_even 0.12, down 0.12
    for mode, expected in (("half_up", "0.13"), ("half_even", "0.12"), ("down", "0.12")):
        rules = spa_rules(
            basis="flat_per_unit",
            price_check_mode="ignore",
            rounding=RoundingRule(mode=mode, scale=2, timing="per_line"),
        )
        r = one(line(quantity=D(1)), [version(rules=rules, items=(item(flat_amount=D("0.125")),))])
        assert r.claim_amount == Decimal(expected), mode


def test_adapter_registry_mechanism() -> None:
    v = version(rules=spa_rules(adapter="test_half"))
    with pytest.raises(UnknownAdapter):
        one(line(), [v])

    def half_of_difference(a: AdapterInput) -> Decimal:
        assert a.reference_cost is not None and a.item.deviated_cost is not None
        return (a.reference_cost - a.item.deviated_cost) / 2

    adapters.REGISTRY["test_half"] = half_of_difference
    try:
        r = one(line(), [v])
    finally:
        del adapters.REGISTRY["test_half"]
    assert (r.claim_unit_amount, r.claim_amount) == (D("11.00"), D("1100.00"))
    assert step(r, "calc")["adapter"] == "test_half"
    assert adapters.REGISTRY == {}


def test_negative_claims_allowed_flag_absent() -> None:
    from rebate_engine.models import PriorClaim
    from tests.engine_golden.builders import HALF_UP_2

    pc = PriorClaim(
        agreement_id="A1",
        agreement_version_id="V1",
        claim_uom="EA",
        quantity=D(10),
        unit_amount=D("2.505"),
        claim_amount=D("25.05"),
        rounding=HALF_UP_2,
    )
    ret = line(
        quantity=D(-3),
        return_original=ReturnOriginal(line_id="O", quantity=D(10), returned_quantity=D(3), prior_claim=pc),
    )
    settings = (replace(ManufacturerSettings(manufacturer_id="M1"), allow_negative_claims=True),)
    r = one(ret, manufacturers=settings)
    assert (r.status, r.claim_amount, r.flags) == (Status.ADJUSTMENT, D("-7.52"), ())  # -7.515 half-up


def test_invalid_version_or_master_data_fails_loudly() -> None:
    no_rate = version(rules=spa_rules(basis="percent_of_sell"), items=(item(rate=None),))
    with pytest.raises(EngineRuleError, match="rate required"):
        one(line(), [no_rate])
    with pytest.raises(EngineRuleError, match="base UoM"):
        one(line(base_uom=None))
    bad = (UomConversion(product_id="P1", from_uom="BX", to_uom="EA", factor=D(0)),)
    with pytest.raises(EngineRuleError, match="non-positive"):
        one(line(uom="BX"), conversions=bad)


def test_return_adjustment_converts_to_claim_uom() -> None:
    from rebate_engine.models import PriorClaim
    from tests.engine_golden.builders import HALF_UP_2

    pc = PriorClaim(
        agreement_id="A1",
        agreement_version_id="V1",
        claim_uom="FT",
        quantity=D(1500),
        unit_amount=D("0.04"),
        claim_amount=D("60.00"),
        rounding=HALF_UP_2,
    )
    orig = ReturnOriginal(line_id="O", quantity=D(3), returned_quantity=D(1), prior_claim=pc)
    ret = line(quantity=D(-1), uom="RL", return_original=orig)
    assert one(ret).exception_code is Code.MISSING_UOM_CONVERSION
    conv = (UomConversion(product_id="P1", from_uom="RL", to_uom="FT", factor=D(500)),)
    r = one(ret, conversions=conv)
    # 1 RL = 500 FT returned x 0.04 = -20.00
    assert (r.status, r.claim_amount, r.eligible_quantity) == (Status.ADJUSTMENT, D("-20.00"), D(-500))


def test_return_adjustment_rounds_per_line_with_prior_claim_rule() -> None:
    # §6/§7: prior claim rounded `down`, per_document: the adjustment is still one line, -3 x 2.505 = -7.515
    from rebate_engine.models import PriorClaim

    pc = PriorClaim(
        agreement_id="A1",
        agreement_version_id="V1",
        claim_uom="EA",
        quantity=D(10),
        unit_amount=D("2.505"),
        claim_amount=D("25.05"),
        rounding=RoundingRule(mode="down", scale=2, timing="per_document"),
    )
    orig = ReturnOriginal(line_id="O", quantity=D(10), returned_quantity=D(3), prior_claim=pc)
    r = one(line(quantity=D(-3), return_original=orig))
    assert r.claim_amount == D("-7.51")
    assert (step(r, "round")["mode"], step(r, "round")["timing"]) == ("down", "per_line")


def test_outside_claim_window_keeps_amount_but_does_not_consume_cap() -> None:
    # §4.2: cap 100, u = 22.00. A (05-01, deadline 06-30 < run 07-10) q60 -> outside window, 1,320.00 kept;
    # B (06-10) q60 -> remaining still 100 -> eligible 60, 1,320.00, not partial.
    v = version(items=(item(max_quantity=D(100)),))
    a = line(line_id="A", document_number="INV-A", invoice_date=date(2026, 5, 1), quantity=D(60))
    b = line(line_id="B", document_number="INV-B", quantity=D(60))
    ra, rb = calculate_spa(SpaRunInput(run_date=RUN, lines=(b, a), versions=(v,), manufacturers=(mfr(),)))
    assert (ra.line_id, ra.exception_code, ra.claim_amount) == ("A", Code.OUTSIDE_CLAIM_WINDOW, D("1320.00"))
    assert (rb.status, rb.eligible_quantity, rb.claim_amount, rb.flags) == (
        Status.ELIGIBLE,
        D(60),
        D("1320.00"),
        (),
    )


def test_claim_window_period_end_is_rejected_for_spa_lines() -> None:
    from rebate_engine.models import EngineRuleError

    with pytest.raises(EngineRuleError, match="rebates only"):
        one(line(), manufacturers=(mfr(claim_window_basis="period_end"),))


def test_rebate_deadline_is_period_end_plus_days() -> None:
    from rebate_engine.deadlines import rebate_deadline

    # hand derivation: 2026-12-31 + 60 days = 2027-03-01 (Jan 31 + Feb 28 = 59 -> 2027-03-01)
    assert rebate_deadline(date(2026, 12, 31), 60) == date(2027, 3, 1)
    assert rebate_deadline(date(2027, 12, 31), 60) == date(2028, 2, 29)  # Jan 31 + 29 = 60 -> leap Feb 29
