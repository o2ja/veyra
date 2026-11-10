"""Parse the agreement payload shape (BUSINESS_LOGIC.md §3) into engine models.

Strict: unknown enum values, wrong types, floats and unsupported schema versions raise ``RulesError``.
The full draft validation (cross-field rules, overlaps, master-data existence) is the AgreementValidator's
job; this module only refuses what the engine could not evaluate exactly.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from rebate_engine.models import (
    CustomerScope,
    EngineRuleError,
    RebateRules,
    RebateVersion,
    RoundingRule,
    SpaItem,
    SpaRules,
    SpaVersion,
    Tier,
)
from rebate_engine.money import to_decimal

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


class RulesError(EngineRuleError):
    pass


def _get(obj: Mapping[str, object], key: str) -> object:
    if key not in obj:
        raise RulesError(f"missing field: {key}")
    return obj[key]


def _map(obj: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = _get(obj, key)
    if not isinstance(value, Mapping):
        raise RulesError(f"{key} must be an object")
    return value


def _enum(obj: Mapping[str, object], key: str, allowed: tuple[str, ...]) -> str:
    value = _get(obj, key)
    if value not in allowed:
        raise RulesError(f"{key} must be one of {allowed}, got {value!r}")
    return str(value)


def _bool(obj: Mapping[str, object], key: str) -> bool:
    value = _get(obj, key)
    if not isinstance(value, bool):
        raise RulesError(f"{key} must be a boolean")
    return value


def _int(obj: Mapping[str, object], key: str) -> int:
    value = _get(obj, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RulesError(f"{key} must be an integer")
    return value


def _str(obj: Mapping[str, object], key: str) -> str:
    value = _get(obj, key)
    if not isinstance(value, str) or not value.strip():
        raise RulesError(f"{key} must be a non-empty string")
    return value


def _opt_str(obj: Mapping[str, object], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RulesError(f"{key} must be a non-empty string or null")
    return value


def _dec(value: object, key: str) -> Decimal:
    if not isinstance(value, str):  # payload money/rates are strings (§3.3); never floats
        raise RulesError(f"{key} must be a decimal string")
    try:
        return to_decimal(value)
    except (TypeError, ValueError) as exc:
        raise RulesError(f"{key}: {exc}") from None


def _opt_dec(obj: Mapping[str, object], key: str) -> Decimal | None:
    value = obj.get(key)
    return None if value is None else _dec(value, key)


def _date(obj: Mapping[str, object], key: str) -> date:
    value = _str(obj, key)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise RulesError(f"{key} must be an ISO date") from None


def _schema_version(obj: Mapping[str, object], key: str) -> int:
    version = _int(obj, key)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RulesError(f"unsupported {key}: {version}")
    return version


def parse_rounding(obj: Mapping[str, object]) -> RoundingRule:
    scale = _int(obj, "scale")
    if not 0 <= scale <= 6:
        raise RulesError("rounding.scale must be 0..6")
    return RoundingRule(
        mode=_enum(obj, "mode", ("half_up", "half_even", "down")),
        scale=scale,
        timing=_enum(obj, "timing", ("per_line", "per_document")),
    )


def parse_spa_rules(rules: Mapping[str, object]) -> SpaRules:
    if rules.get("type") != "spa":
        raise RulesError("rules.type must be 'spa'")
    calc = _map(rules, "calculation")
    price = _map(rules, "price_check")
    tolerance = _dec(_get(price, "tolerance"), "price_check.tolerance")
    if tolerance < 0:
        raise RulesError("price_check.tolerance must be >= 0")
    return SpaRules(
        schema_version=_schema_version(rules, "schema_version"),
        date_basis=_enum(rules, "date_basis", ("invoice_date", "ship_date")),
        basis=_enum(
            calc,
            "basis",
            ("price_difference", "percent_of_sell", "percent_of_cost", "flat_per_unit", "guaranteed_margin"),
        ),
        cost_source=_enum(calc, "cost_source", ("agreement_reference", "line_cost", "replacement_cost")),
        customer_scope_mode=_enum(_map(rules, "customer_scope"), "mode", ("all", "listed")),
        product_scope_mode=_enum(_map(rules, "product_scope"), "mode", ("listed",)),
        spa_reference_required=_bool(_map(rules, "spa_reference"), "required"),
        price_check_mode=_enum(price, "mode", ("flag", "ignore")),
        price_tolerance=tolerance,
        cap_scope=_enum(_map(rules, "quantity"), "cap_scope", ("per_item", "per_agreement", "none")),
        claim_uom=_opt_str(rules, "claim_uom"),
        rounding=parse_rounding(_map(rules, "rounding")),
        adapter=_opt_str(rules, "adapter"),
    )


def parse_rebate_rules(rules: Mapping[str, object]) -> RebateRules:
    if rules.get("type") != "rebate":
        raise RulesError("rules.type must be 'rebate'")
    period = _map(rules, "measurement_period")
    start, end = _date(period, "start"), _date(period, "end")
    if end < start:
        raise RulesError("measurement_period.end must be >= start")
    return RebateRules(
        schema_version=_schema_version(rules, "schema_version"),
        volume_basis=_enum(rules, "volume_basis", ("purchases", "sales")),
        period_start=start,
        period_end=end,
        tier_mode=_enum(rules, "tier_mode", ("retroactive", "incremental")),
        tier_metric=_enum(rules, "tier_metric", ("amount", "quantity")),
        product_scope_mode=_enum(_map(rules, "product_scope"), "mode", ("all", "listed")),
        spa_lines=_enum(rules, "spa_lines", ("include", "qualify_only", "exclude")),
        returns=_enum(rules, "returns", ("reduce_volume",)),
        rounding=parse_rounding(_map(rules, "rounding")),
    )


def _resolve(ids: Mapping[str, str], code: str, kind: str) -> str:
    if code not in ids:
        raise RulesError(f"unresolved {kind}: {code!r}")
    return ids[code]


def _header(payload: Mapping[str, object], agreement_type: str) -> Mapping[str, object]:
    _schema_version(payload, "payload_schema_version")
    header = _map(payload, "header")
    if header.get("agreement_type") != agreement_type:
        raise RulesError(f"header.agreement_type must be {agreement_type!r}")
    return header


def _list(payload: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, Mapping) for v in value):
        raise RulesError(f"{key} must be a list of objects")
    return value


def spa_version_from_payload(
    payload: Mapping[str, object],
    *,
    version_id: str,
    agreement_id: str,
    manufacturer_id: str,
    product_ids: Mapping[str, str],
    customer_ids: Mapping[str, str],
) -> SpaVersion:
    """Build an ``SpaVersion`` from a payload; SKUs/customer codes are resolved through the given maps."""
    header = _header(payload, "spa")
    valid_from, valid_to = _date(header, "valid_from"), _date(header, "valid_to")
    if valid_to < valid_from:
        raise RulesError("valid_to must be >= valid_from")
    items = tuple(
        SpaItem(
            product_id=_resolve(product_ids, _str(i, "sku"), "sku"),
            reference_cost=_opt_dec(i, "reference_cost"),
            deviated_cost=_opt_dec(i, "deviated_cost"),
            sell_price=_opt_dec(i, "sell_price"),
            rate=_opt_dec(i, "rate"),
            flat_amount=_opt_dec(i, "flat_amount"),
            max_quantity=_opt_dec(i, "max_quantity"),
            claim_uom=_opt_str(i, "claim_uom"),
        )
        for i in _list(payload, "items")
    )
    if len({i.product_id for i in items}) != len(items):
        raise RulesError("duplicate item product")
    customers = tuple(
        CustomerScope(
            customer_id=_resolve(customer_ids, _str(c, "customer_code"), "customer"),
            scope=_enum(c, "scope", ("include", "exclude")),
        )
        for c in _list(payload, "customers")
    )
    return SpaVersion(
        version_id=version_id,
        agreement_id=agreement_id,
        external_number=_str(header, "external_number"),
        manufacturer_id=manufacturer_id,
        valid_from=valid_from,
        valid_to=valid_to,
        priority=_int(header, "priority"),
        rules=parse_spa_rules(_map(payload, "rules")),
        items=items,
        customers=customers,
    )


def rebate_version_from_payload(
    payload: Mapping[str, object],
    *,
    version_id: str,
    manufacturer_id: str,
    product_ids: Mapping[str, str],
) -> RebateVersion:
    header = _header(payload, "rebate")
    tiers = tuple(
        Tier(
            tier_number=_int(t, "tier_number"),
            lower_bound=_dec(_get(t, "lower_bound"), "lower_bound"),
            rate=_dec(_get(t, "rate"), "rate"),
        )
        for t in _list(payload, "tiers")
    )
    return RebateVersion(
        version_id=version_id,
        external_number=_str(header, "external_number"),
        manufacturer_id=manufacturer_id,
        rules=parse_rebate_rules(_map(payload, "rules")),
        tiers=tiers,
        product_ids=tuple(_resolve(product_ids, _str(i, "sku"), "sku") for i in _list(payload, "items")),
    )
