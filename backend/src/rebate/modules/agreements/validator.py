"""AgreementValidator (BUSINESS_LOGIC.md §3.3): one validator for manual, spreadsheet-template and AI drafts.

Errors block submit and approval; warnings must be acknowledged at submit. Rules:

* structure: ``schemas.Payload`` (strict types, decimal strings fitting their columns, unknown fields
  rejected; ``deviated_cost >= 0``, ``max_quantity > 0`` and rates in [0, 1] are type constraints);
* engine conformance: the rules object is parsed by ``rebate_engine.rules`` (``cap_scope = per_agreement``,
  ``tier_metric = quantity`` -> ``not_supported_until_release_2``; ``incremental`` + ``qualify_only`` ->
  ``incompatible``) and the resolved payload is converted with ``spa_version_from_payload`` /
  ``rebate_version_from_payload``, so nothing is approved that the engine would refuse;
* dates (``valid_to >= valid_from``), tiers (1..n, first = 0, strictly increasing), per-basis required
  item fields, SPA needs items, duplicates per product/customer, manufacturer exists, SKUs and customer codes
  resolve (reference report), relation to the agreement (exists / amends / cancelled), no overlap with the
  agreement's other active or expired versions.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.modules import ingestion, masterdata
from rebate.modules.agreements.models import Agreement, AgreementVersion
from rebate.modules.agreements.schemas import Payload, RebateRules, SpaRules
from rebate_engine import adapters
from rebate_engine.rules import (
    RulesError,
    UnsupportedRuleError,
    parse_rebate_rules,
    parse_spa_rules,
    rebate_version_from_payload,
    spa_version_from_payload,
)

MAX_REPORTED_ERRORS = 1000
COST_FIELDS = {  # basis -> item fields required (reference_cost only when cost_source = agreement_reference)
    "price_difference": ("deviated_cost", "reference_cost"),
    "percent_of_sell": ("rate",),
    "percent_of_cost": ("rate", "reference_cost"),
    "guaranteed_margin": ("rate", "reference_cost"),
    "flat_per_unit": ("flat_amount",),
}
_CODES = {"missing": "required", "extra_forbidden": "unknown_field", "literal_error": "invalid_choice"}


@dataclass
class Draftish:
    """What the validator needs to know about a draft besides its payload."""

    agreement_id: UUID | None
    amends_version_id: UUID | None


@dataclass
class Resolved:
    payload: Payload
    manufacturer_id: UUID
    product_ids: list[UUID]  # one per payload item, in order
    customers: list[tuple[UUID, str]]  # (customer_id, scope), in order


@dataclass
class Result:
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    unresolved_customers: set[str] = field(default_factory=set)
    unresolved_skus: set[str] = field(default_factory=set)
    resolved: Resolved | None = None

    def error(self, path: str, code: str, message: str) -> None:
        self.errors.append({"field": path, "code": code, "message": message})

    def warn(self, path: str, code: str, message: str) -> None:
        self.warnings.append({"field": path, "code": code, "message": message, "id": f"{code}:{path}"})

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_json(self, revision: int) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "revision": revision,
            "validated_at": clock.now().isoformat(),
            "error_count": len(self.errors),
            "errors": self.errors[:MAX_REPORTED_ERRORS],
            "warnings": self.warnings,
            "references": {
                "unresolved_customers": sorted(self.unresolved_customers),
                "unresolved_skus": sorted(self.unresolved_skus),
            },
        }


def _path(loc: tuple[int | str, ...]) -> str:
    out = "payload"
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part not in ("spa", "rebate"):  # discriminated-union tags are not payload keys
            out += f".{part}"
    return out


def _code(text: str) -> str:
    return ingestion.canonical_code(text)


def validate(db: Session, tenant_id: UUID, draft: Draftish, payload: dict[str, Any], today: date) -> Result:
    result = Result()
    try:
        model = Payload.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            result.error(_path(err["loc"]), _CODES.get(err["type"], err["type"]), err["msg"])
        return result

    h, rules = model.header, model.rules
    valid_from, valid_to = date.fromisoformat(h.valid_from), date.fromisoformat(h.valid_to)
    if h.agreement_type != rules.type:
        result.error("payload.rules.type", "mismatch", "The rules type must equal header.agreement_type.")
    if valid_to < valid_from:
        result.error("payload.header.valid_to", "invalid_range", "Must be on or after valid_from.")
    elif valid_to < today:
        result.warn("payload.header.valid_to", "already_expired", "The agreement has already ended.")
    _rules(result, rules, payload["rules"], valid_from, valid_to)

    index = masterdata.master_index(db, tenant_id)
    manufacturer_id = index.manufacturers.get(_code(h.manufacturer_code))
    if manufacturer_id is None:
        result.error("payload.header.manufacturer_code", "unresolved", "No manufacturer with this code.")

    if isinstance(rules, SpaRules):
        _spa_shape(result, model, rules)
    else:
        _rebate_shape(result, model, rules)
    product_ids = _items(result, model, index, manufacturer_id)
    customers = _customers(result, model, index)
    if manufacturer_id is not None:
        _relation(db, result, tenant_id, draft, model, UUID(manufacturer_id), valid_from, valid_to)

    if result.valid and manufacturer_id is not None:
        _engine_conformance(result, payload, model, manufacturer_id, product_ids, customers)
    if result.valid and manufacturer_id is not None:
        result.resolved = Resolved(
            model,
            UUID(manufacturer_id),
            [UUID(p) for p in product_ids],
            [(UUID(c), ref.scope) for c, ref in zip(customers, model.customers, strict=True)],
        )
    return result


def _rules(
    result: Result, rules: SpaRules | RebateRules, raw: dict[str, Any], start: date, end: date
) -> None:
    if isinstance(rules, RebateRules):
        period = rules.measurement_period
        p_start, p_end = date.fromisoformat(period.start), date.fromisoformat(period.end)
        if p_end < p_start:
            result.error(
                "payload.rules.measurement_period.end", "invalid_range", "Must be on or after start."
            )
            return
        if (p_start, p_end) != (start, end):
            result.warn(
                "payload.rules.measurement_period",
                "measurement_period_mismatch",
                "The measurement period differs from the agreement validity.",
            )
    elif rules.adapter is not None and rules.adapter not in adapters.REGISTRY:
        result.error("payload.rules.adapter", "unknown_adapter", "No adapter with this name is registered.")
    parse: Callable[[dict[str, Any]], object] = parse_spa_rules if rules.type == "spa" else parse_rebate_rules
    try:
        parse(raw)
    except UnsupportedRuleError as exc:
        result.error(f"payload.rules.{exc.field}", exc.code, str(exc))
    except RulesError as exc:
        if "incompatible" in str(exc):
            result.error("payload.rules.spa_lines", "incompatible", str(exc))
        else:
            result.error("payload.rules", "invalid", str(exc))


def _spa_shape(result: Result, model: Payload, rules: SpaRules) -> None:
    if not model.items:
        result.error("payload.items", "required", "An SPA needs at least one item.")
    if model.tiers:
        result.error("payload.tiers", "not_applicable", "SPA agreements have no tiers.")
    if rules.customer_scope.mode == "listed" and not model.customers:
        result.error("payload.customers", "required", "customer_scope 'listed' needs at least one customer.")
    if rules.customer_scope.mode == "all" and model.customers:
        result.error("payload.customers", "not_applicable", "customer_scope 'all' takes no customer list.")
    needed = COST_FIELDS[rules.calculation.basis]
    if rules.calculation.cost_source != "agreement_reference":
        needed = tuple(f for f in needed if f != "reference_cost")
    for i, item in enumerate(model.items):
        for name in needed:
            if getattr(item, name) is None:
                result.error(
                    f"payload.items[{i}].{name}",
                    "required",
                    f"Required for basis {rules.calculation.basis} (cost source {rules.calculation.cost_source}).",
                )


def _rebate_shape(result: Result, model: Payload, rules: RebateRules) -> None:
    if model.customers:
        result.error("payload.customers", "not_applicable", "Rebate agreements have no customer list.")
    if rules.product_scope.mode == "listed" and not model.items:
        result.error("payload.items", "required", "product_scope 'listed' needs at least one item.")
    if rules.product_scope.mode == "all" and model.items:
        result.error("payload.items", "not_applicable", "product_scope 'all' takes no item list.")
    if not model.tiers:
        result.error("payload.tiers", "required", "A rebate needs at least one tier.")
        return
    previous = None
    for i, tier in enumerate(model.tiers):
        bound = Decimal(tier.lower_bound)
        if tier.tier_number != i + 1:
            result.error(
                f"payload.tiers[{i}].tier_number", "invalid", f"Tiers are numbered 1..n; expected {i + 1}."
            )
        if i == 0 and bound != 0:
            result.error("payload.tiers[0].lower_bound", "invalid", "Tier 1 must start at 0.")
        if previous is not None and bound <= previous:
            result.error(
                f"payload.tiers[{i}].lower_bound", "not_increasing", "Must be above the previous tier."
            )
        previous = bound


def _items(
    result: Result, model: Payload, index: masterdata.MasterIndex, manufacturer_id: str | None
) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(model.items):
        sku = _code(item.sku)
        found = index.products.get(sku)
        if found is None:
            result.unresolved_skus.add(sku)
            result.error(f"payload.items[{i}].sku", "unresolved", f"Unknown SKU {sku}.")
            ids.append("")
            continue
        product_id, product_manufacturer = found
        if product_id in seen:
            result.error(f"payload.items[{i}].sku", "duplicate", f"{sku} is listed more than once.")
        seen.add(product_id)
        if manufacturer_id is not None and product_manufacturer != manufacturer_id:
            result.warn(
                f"payload.items[{i}].sku",
                "product_manufacturer_mismatch",
                f"{sku} belongs to another manufacturer.",
            )
        ids.append(product_id)
    return ids


def _customers(result: Result, model: Payload, index: masterdata.MasterIndex) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for i, ref in enumerate(model.customers):
        code = _code(ref.customer_code)
        customer_id = index.customers.get(code)
        if customer_id is None:
            result.unresolved_customers.add(code)
            result.error(f"payload.customers[{i}].customer_code", "unresolved", f"Unknown customer {code}.")
            ids.append("")
            continue
        if customer_id in seen:
            result.error(
                f"payload.customers[{i}].customer_code", "duplicate", f"{code} is listed more than once."
            )
        seen.add(customer_id)
        ids.append(customer_id)
    return ids


def _relation(
    db: Session,
    result: Result,
    tenant_id: UUID,
    draft: Draftish,
    model: Payload,
    manufacturer_id: UUID,
    valid_from: date,
    valid_to: date,
) -> None:
    h = model.header
    if draft.agreement_id is None:
        exists = db.scalar(
            select(Agreement.id).where(
                Agreement.tenant_id == tenant_id,
                Agreement.manufacturer_id == manufacturer_id,
                Agreement.external_number == h.external_number,
            )
        )
        if exists is not None:
            result.error(
                "payload.header.external_number",
                "agreement_exists",
                "An agreement with this number exists for the manufacturer; create an amendment draft instead.",
            )
        return
    agreement = db.scalar(
        select(Agreement).where(Agreement.tenant_id == tenant_id, Agreement.id == draft.agreement_id)
    )
    if agreement is None:
        result.error("agreement_id", "not_found", "The agreement does not exist.")
        return
    if agreement.cancelled_at is not None:
        result.error("agreement_id", "agreement_cancelled", "The agreement was cancelled.")
    for path, value, expected in (
        ("payload.header.agreement_type", h.agreement_type, agreement.agreement_type),
        ("payload.header.external_number", h.external_number, agreement.external_number),
        ("payload.header.manufacturer_code", manufacturer_id, agreement.manufacturer_id),
        ("payload.header.name", h.name, agreement.name),  # the name lives on the agreement, not the version
    ):
        if value != expected:
            result.error(path, "mismatch", "Must match the agreement being amended.")
    if valid_to < valid_from:
        return
    versions = db.scalars(
        select(AgreementVersion).where(
            AgreementVersion.tenant_id == tenant_id,
            AgreementVersion.agreement_id == agreement.id,
            AgreementVersion.status.in_(("active", "expired")),
        )
    ).all()
    amended = None
    for v in versions:
        if v.id == draft.amends_version_id:
            amended = v
        elif v.valid_from <= valid_to and valid_from <= v.valid_to:
            result.error(
                "payload.header.valid_from",
                "version_overlap",
                f"Overlaps version {v.version_number} ({v.valid_from} to {v.valid_to}).",
            )
    if draft.amends_version_id is not None:
        if amended is None:
            result.error("amends_version_id", "stale_version", "The amended version is no longer in force.")
        elif not (amended.valid_from <= valid_to and valid_from <= amended.valid_to):
            result.error(
                "payload.header.valid_from",
                "outside_amended_version",
                "The amendment must overlap the period of the version it amends.",
            )


def _engine_conformance(
    result: Result,
    payload: dict[str, Any],
    model: Payload,
    manufacturer_id: str,
    product_ids: list[str],
    customers: list[str],
) -> None:
    products = {item["sku"]: pid for item, pid in zip(payload.get("items", []), product_ids, strict=True)}
    try:
        if model.rules.type == "spa":
            codes = {
                c["customer_code"]: cid
                for c, cid in zip(payload.get("customers", []), customers, strict=True)
            }
            spa_version_from_payload(
                payload,
                version_id="draft",
                agreement_id="draft",
                manufacturer_id=manufacturer_id,
                product_ids=products,
                customer_ids=codes,
            )
        else:
            rebate_version_from_payload(
                payload, version_id="draft", manufacturer_id=manufacturer_id, product_ids=products
            )
    except RulesError as exc:  # the schema above should make this unreachable; never approve if not
        result.error("payload", "engine_rejected", str(exc))
