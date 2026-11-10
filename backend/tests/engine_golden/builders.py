"""Small in-memory input builders for golden/unit tests (defaults are a valid price-difference SPA)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from rebate_engine.models import (
    CustomerScope,
    ManufacturerSettings,
    RebateRules,
    RebateVersion,
    RoundingRule,
    SalesLine,
    SpaItem,
    SpaRules,
    SpaVersion,
    Tier,
    VolumeLine,
)

D = Decimal
HALF_UP_2 = RoundingRule(mode="half_up", scale=2, timing="per_line")


def spa_rules(**kw: Any) -> SpaRules:
    base = SpaRules(
        schema_version=1,
        date_basis="invoice_date",
        basis="price_difference",
        cost_source="agreement_reference",
        customer_scope_mode="listed",
        product_scope_mode="listed",
        spa_reference_required=False,
        price_check_mode="flag",
        price_tolerance=D("0.005"),
        cap_scope="per_item",
        claim_uom=None,
        rounding=HALF_UP_2,
        adapter=None,
    )
    return replace(base, **kw)


def item(**kw: Any) -> SpaItem:
    base = SpaItem(
        product_id="P1", reference_cost=D("100.00"), deviated_cost=D("78.00"), sell_price=D("88.00")
    )
    return replace(base, **kw)


def version(items: tuple[SpaItem, ...] | None = None, **kw: Any) -> SpaVersion:
    rules = kw.pop("rules", spa_rules())
    base = SpaVersion(
        version_id="V1",
        agreement_id="A1",
        external_number="SPA-1",
        manufacturer_id="M1",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        priority=0,
        rules=rules,
        items=items if items is not None else (item(),),
        customers=(CustomerScope(customer_id="C1", scope="include"),),
    )
    return replace(base, **kw)


def line(**kw: Any) -> SalesLine:
    base = SalesLine(
        line_id="L1",
        document_number="INV-1",
        line_number=1,
        invoice_date=date(2026, 6, 10),
        ship_date=date(2026, 6, 10),
        customer_id="C1",
        product_id="P1",
        manufacturer_id="M1",
        base_uom="EA",
        quantity=D(100),
        uom="EA",
        unit_price=D("88.00"),
        spa_reference="SPA-1",
    )
    return replace(base, **kw)


def mfr(**kw: Any) -> ManufacturerSettings:
    base = ManufacturerSettings(
        manufacturer_id="M1", conflict_policy="flag", claim_window_basis="invoice_date", claim_window_days=60
    )
    return replace(base, **kw)


THREE_TIERS = (
    Tier(tier_number=1, lower_bound=D("0"), rate=D("0.015")),
    Tier(tier_number=2, lower_bound=D("1000000"), rate=D("0.025")),
    Tier(tier_number=3, lower_bound=D("2000000"), rate=D("0.035")),
)


def rebate(tiers: tuple[Tier, ...] = THREE_TIERS, **rules_kw: Any) -> RebateVersion:
    rules = RebateRules(
        schema_version=1,
        volume_basis="purchases",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        tier_mode="retroactive",
        tier_metric="amount",
        product_scope_mode="all",
        spa_lines="include",
        returns="reduce_volume",
        rounding=HALF_UP_2,
    )
    return RebateVersion(
        version_id="R1",
        external_number="REB-1",
        manufacturer_id="M1",
        rules=replace(rules, **rules_kw),
        tiers=tiers,
    )


def vline(line_id: str, amount: str, day: date = date(2026, 3, 1), **kw: Any) -> VolumeLine:
    base = VolumeLine(
        line_id=line_id, line_date=day, manufacturer_id="M1", product_id="P1", amount=D(amount), quantity=D(1)
    )
    return replace(base, **kw)


def step(result: Any, name: str) -> dict[str, Any]:
    """Last trace step called ``name`` (traces are JSON; tests read them loosely)."""
    found = [s for s in result.trace["steps"] if s["step"] == name]
    assert found, f"no {name} step"
    out: dict[str, Any] = found[-1]
    return out


def warnings(result: Any) -> list[str]:
    out: list[str] = result.trace.get("warnings", [])
    return out
