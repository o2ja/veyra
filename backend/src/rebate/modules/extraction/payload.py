"""Verified extraction -> agreement payload (AI_PROCESSING.md §4.7, §5). Pure apart from the master-data
name lookups passed in. Only fields that survived evidence verification and the two-pass agreement carry a
value; conflicting, unsupported and not-stated fields are left out so the AgreementValidator reports them as
missing and the reviewer sets them. Values come from the verified *claimed* value through the deterministic
parsers (``verification.canonical``), never from the model's quote.

Structural defaults that no contract states and that the engine needs (price check, cap scope, product scope,
tier metric, returns treatment) are the documented defaults of ``testdata`` manual payloads; the reviewer sees
them in the draft like any other value. ``header.name`` is derived (number + manufacturer) for the same reason.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

from rebate.modules.extraction import parsers as p
from rebate.modules.extraction.verification import Kind, MergedField, canonical, spec_for

ROW = re.compile(r"^(items|customers|tiers|exclusions_text)\[(.*?)\](?:\.(\w+))?$")
PRICE_TOLERANCE = "0.005"


name_key = p.name_key  # one definition for the master-data lookup and the pass comparison


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    return value


def _val(f: MergedField) -> Any:
    """The verified value in storage form: parsed by the deterministic parser; names keep their printed form."""
    if f.value is None:
        return None
    spec = spec_for(f.key)
    return f.value if spec.kind in (Kind.name, Kind.text) else jsonable(canonical(spec, f.value))


def value_of(fields: dict[str, MergedField], key: str) -> Any:
    f = fields.get(key)
    return None if f is None else _val(f)


def normalise_rates(raw: dict[str, Any]) -> dict[str, Any]:
    """``"2.5%"`` -> ``"0.025"`` for item and tier rates: percentages are converted here, not by the model."""
    out = {**raw}
    for section in ("items", "tiers"):
        rows = []
        for row in raw.get(section) or []:
            row = dict(row) if isinstance(row, dict) else row
            rate = row.get("rate") if isinstance(row, dict) else None
            if isinstance(rate, dict) and isinstance(rate.get("value"), str):
                found = p.percents(rate["value"])
                if rate["value"].strip().endswith("%") and len(found) == 1:
                    row["rate"] = {**rate, "value": format(found[0], "f")}
            rows.append(row)
        if section in raw:
            out[section] = rows
    return out


def rows_of(fields: dict[str, MergedField], section: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(row key, {field: canonical json value})]`` in first-seen order; all-empty rows are dropped."""
    rows: dict[str, dict[str, Any]] = {}
    for key, f in fields.items():
        m = ROW.match(key)
        if not m or m.group(1) != section or m.group(3) is None:
            continue
        row = rows.setdefault(m.group(2), {})
        row[m.group(3)] = _val(f)
    return [(k, r) for k, r in rows.items() if any(v is not None for v in r.values())]


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def build_payload(
    fields: dict[str, MergedField],
    *,
    manufacturer_codes: dict[str, str],
    customer_codes: dict[str, str],
) -> dict[str, Any]:
    def v(key: str) -> Any:
        return value_of(fields, key)

    kind = v("header.agreement_type")
    mfr = v("header.manufacturer_name")
    number = v("header.external_number")
    header: dict[str, Any] = {"priority": 0}
    _put(header, "agreement_type", kind)
    if (
        mfr is not None
    ):  # an unresolved name stays visible: the validator reports "No manufacturer with this code"
        header["manufacturer_code"] = manufacturer_codes.get(name_key(mfr), mfr)[:50]
    _put(header, "external_number", number)
    if number is not None or mfr is not None:
        header["name"] = " ".join(x for x in (number, mfr) if x)[:200]
    _put(header, "valid_from", v("header.valid_from"))
    _put(header, "valid_to", v("header.valid_to"))

    customers = []
    for _key, row in rows_of(fields, "customers"):
        name = row.get("name")
        ref: dict[str, Any] = {}
        if name is not None:
            ref["customer_code"] = customer_codes.get(name_key(name), name)[:50]
        _put(ref, "scope", row.get("scope"))
        customers.append(ref)

    rounding = v("rules.rounding")
    rules: dict[str, Any] = {"schema_version": 1}
    _put(rules, "type", kind)
    _put(rules, "rounding", rounding)
    if kind == "spa":
        _put(rules, "date_basis", v("header.date_basis"))
        calc: dict[str, Any] = {}
        _put(calc, "basis", v("rules.calculation_basis"))
        _put(calc, "cost_source", v("rules.cost_source"))
        if calc:
            rules["calculation"] = calc
        if customers:
            rules["customer_scope"] = {"mode": "listed"}
        rules["product_scope"] = {"mode": "listed"}
        ref_required = v("rules.spa_reference_required")
        if ref_required is not None:
            rules["spa_reference"] = {"required": ref_required}
        rules["price_check"] = {"mode": "flag", "tolerance": PRICE_TOLERANCE}
        rules["quantity"] = {"cap_scope": "per_item"}
        _put(rules, "claim_uom", v("rules.claim_uom"))
    elif kind == "rebate":
        _put(rules, "volume_basis", v("rules.volume_basis"))
        _put(rules, "measurement_period", v("rules.measurement_period"))
        _put(rules, "tier_mode", v("rules.tier_mode"))
        rules["tier_metric"] = "amount"
        rules["product_scope"] = {"mode": "all"}
        _put(rules, "spa_lines", v("rules.spa_lines_policy"))
        rules["returns"] = "reduce_volume"

    items = []
    for _, row in rows_of(fields, "items"):
        item: dict[str, Any] = {}
        _put(item, "sku", row.get("part_number"))
        for f in ("reference_cost", "deviated_cost", "sell_price", "rate", "flat_amount", "max_quantity"):
            _put(item, f, row.get(f))
        _put(item, "claim_uom", row.get("uom"))
        items.append(item)

    tiers = []
    ordered = sorted(
        rows_of(fields, "tiers"),
        key=lambda kr: Decimal(kr[1]["lower_bound"]) if kr[1].get("lower_bound") is not None else Decimal(-1),
    )
    for n, (_, row) in enumerate(ordered, start=1):
        tier: dict[str, Any] = {"tier_number": n}
        _put(tier, "lower_bound", row.get("lower_bound"))
        _put(tier, "rate", row.get("rate"))
        tiers.append(tier)

    return {
        "payload_schema_version": 1,
        "header": header,
        "rules": rules,
        "customers": customers,
        "items": items,
        "tiers": tiers,
    }
