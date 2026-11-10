"""Rules parsing, rebate edge cases, trace canonicalisation, no-float lint over rebate_engine."""

import ast
import copy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import rebate_engine
from rebate_engine.models import EngineRuleError, Json, Tier
from rebate_engine.rebate import calculate_accrual, incremental_raw, tier_for
from rebate_engine.rules import RulesError, parse_rebate_rules, parse_spa_rules
from rebate_engine.trace import canonical_json, trace_hash
from tests.engine_golden import testdata_inputs as td
from tests.engine_golden.builders import THREE_TIERS, D, rebate, step, vline


@pytest.mark.parametrize("name", [*td.SPA_PAYLOADS, "REB-VOLT-2026", "REB-NWND-2026"])
def test_every_manual_payload_parses(name: str) -> None:
    if name.startswith("SPA"):
        v = td.spa_version(name)
        assert v.items and v.rules.schema_version == 1
    else:
        r = td.rebate_version(name)
        assert [t.lower_bound for t in r.tiers] == [D(0), D(1000000), D(2000000)]


def _spa_rules() -> dict[str, Any]:
    return copy.deepcopy(td.payload("SPA-VOLT-2026-884")["rules"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["price_check"].__setitem__("tolerance", 0.005),  # float
        lambda r: r["price_check"].__setitem__("tolerance", "-1"),
        lambda r: r.__setitem__("date_basis", "order_date"),
        lambda r: r["calculation"].__setitem__("basis", "magic"),
        lambda r: r["rounding"].__setitem__("scale", 7),
        lambda r: r["rounding"].__setitem__("scale", True),
        lambda r: r.__setitem__("schema_version", 2),
        lambda r: r.pop("quantity"),
        lambda r: r["spa_reference"].__setitem__("required", "yes"),
        lambda r: r.__setitem__("type", "rebate"),
    ],
)
def test_spa_rules_reject_invalid(mutate: Any) -> None:
    rules = _spa_rules()
    mutate(rules)
    with pytest.raises(RulesError):
        parse_spa_rules(rules)


def test_payload_rejects_float_money_and_unresolved_codes() -> None:
    p = td.payload("SPA-VOLT-2026-884")
    p["items"][0]["deviated_cost"] = 78.0
    with pytest.raises(RulesError, match="decimal string"):
        td.spa_version("SPA-VOLT-2026-884", p)
    p = td.payload("SPA-VOLT-2026-884")
    p["customers"].append({"customer_code": "NOPE", "scope": "include"})
    with pytest.raises(RulesError, match="unresolved customer"):
        td.spa_version("SPA-VOLT-2026-884", p)


def test_rebate_rules_reject_invalid_period() -> None:
    rules = copy.deepcopy(td.payload("REB-VOLT-2026")["rules"])
    rules["measurement_period"]["end"] = "2025-12-31"
    with pytest.raises(RulesError):
        parse_rebate_rules(rules)


def test_tier_validation() -> None:
    with pytest.raises(EngineRuleError):
        tier_for([Tier(tier_number=1, lower_bound=D(10), rate=D("0.01"))], D(5))
    with pytest.raises(EngineRuleError):
        tier_for([*THREE_TIERS, Tier(tier_number=4, lower_bound=D(2000000), rate=D("0.04"))], D(5))
    with pytest.raises(EngineRuleError):
        tier_for([Tier(tier_number=1, lower_bound=D(0), rate=D("1.5"))], D(5))
    assert tier_for(THREE_TIERS, D(-1)) is None
    assert tier_for(THREE_TIERS, D("999999.99")).tier_number == 1  # type: ignore[union-attr]
    assert tier_for(THREE_TIERS, D(2000000)).tier_number == 3  # type: ignore[union-attr]
    assert incremental_raw(THREE_TIERS, D(2500000)) == D(15000) + D(25000) + D(17500)


def test_rebate_window_scope_and_refusals() -> None:
    lines = [
        vline("in", "100000", date(2026, 6, 1)),
        vline("future", "900000", date(2026, 8, 1)),  # after as_of
        vline("other_mfr", "500000", manufacturer_id="M2"),
        vline("other_product", "500000", product_id="P2"),
    ]
    r = calculate_accrual(rebate(), lines, date(2026, 7, 1), D(0))
    assert r.qualifying_volume == D(600000)  # in + other_product (scope all), not other_mfr / future
    only_p1 = replace(rebate(product_scope_mode="listed"), product_ids=("P1",))
    assert calculate_accrual(only_p1, lines, date(2026, 7, 1), D(0)).qualifying_volume == D(100000)
    assert calculate_accrual(rebate(), lines, date(2025, 12, 31), D(0)).earned_cumulative == 0
    with pytest.raises(EngineRuleError, match="quantity"):
        calculate_accrual(rebate(tier_metric="quantity"), lines, date(2026, 7, 1), D(0))
    with pytest.raises(EngineRuleError, match="incremental"):
        calculate_accrual(
            rebate(tier_mode="incremental", volume_basis="sales", spa_lines="qualify_only"),
            [vline("a", "10", spa_reference="X")],
            date(2026, 7, 1),
            D(0),
        )


def test_spa_line_signals() -> None:
    sales = rebate(volume_basis="sales", spa_lines="qualify_only")
    # §5.4 (owner decision 2026-11-10): either signal suffices; the trace records both signals
    lines = [
        vline("elig_no_ref", "10", spa_eligible=True),  # eligible without reference -> SPA line
        vline("ref_not_elig", "20", spa_reference="X"),  # reference but not eligible -> SPA line
        vline("both", "40", spa_reference="X", spa_eligible=True),
        vline("plain", "100"),
    ]
    r = calculate_accrual(sales, lines, date(2026, 7, 1), D(0))
    assert (r.qualifying_volume, r.payout_volume) == (D(170), D(100))
    assert step(r, "volume")["spa_line_signals"] == [
        {"line_id": "both", "spa_reference": True, "spa_eligible": True},
        {"line_id": "elig_no_ref", "spa_reference": False, "spa_eligible": True},
        {"line_id": "ref_not_elig", "spa_reference": True, "spa_eligible": False},
    ]
    excl = calculate_accrual(rebate(volume_basis="sales", spa_lines="exclude"), lines, date(2026, 7, 1), D(0))
    assert (excl.qualifying_volume, excl.payout_volume) == (D(100), D(100))
    # include: SPA lines qualify and earn whatever the signals say
    inc = rebate(volume_basis="sales", spa_lines="include")
    assert (
        calculate_accrual(inc, [vline("a", "10", spa_eligible=True)], date(2026, 7, 1), D(0)).payout_volume
        == 10
    )
    # purchases basis: spa_lines has no meaning
    purch = rebate(volume_basis="purchases", spa_lines="exclude")
    assert (
        calculate_accrual(purch, [vline("a", "10", spa_reference="X")], date(2026, 7, 1), D(0)).payout_volume
        == 10
    )


def test_canonical_json_is_key_order_independent() -> None:
    a: Json = {"b": "1", "a": [{"y": True, "x": None}]}
    b: Json = {"a": [{"x": None, "y": True}], "b": "1"}
    assert canonical_json(a) == canonical_json(b) == '{"a":[{"x":null,"y":true}],"b":"1"}'
    assert trace_hash(a) == trace_hash(b) and len(trace_hash(a)) == 64


def test_traces_contain_no_floats_and_numbers_are_strings() -> None:
    r = calculate_accrual(rebate(), [vline("a", "990000")], date(2026, 8, 30), D(0))

    def walk(node: object) -> None:
        assert not isinstance(node, float)
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(r.trace)
    assert r.trace["result"] == {"earned_cumulative": "14850.00", "delta": "14850.00", "flags": []}


def test_no_float_in_rebate_engine_source() -> None:
    """Lint: no float literal, float() call, or `float` annotation anywhere in rebate_engine (§1.2)."""
    root = Path(rebate_engine.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{path.name}:{node.lineno} float literal")
            if isinstance(node, ast.Name) and node.id == "float" and path.name != "money.py":
                offenders.append(f"{path.name}:{node.lineno} float name")
    assert offenders == []
    # money.py may only name `float` to reject it
    money = (root / "money.py").read_text(encoding="utf-8")
    assert "isinstance(value, bool | float)" in money


def test_engine_rejects_float_inputs_at_the_money_boundary() -> None:
    from rebate_engine.money import quantize, to_decimal

    with pytest.raises(TypeError):
        to_decimal(1.5)
    with pytest.raises(ValueError):
        quantize(Decimal("1.005"), 2, "ceiling")
    with pytest.raises(ValueError):
        quantize(Decimal("1.005"), 7, "half_up")
