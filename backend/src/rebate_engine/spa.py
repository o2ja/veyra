"""SPA / ship-and-debit claim calculation (BUSINESS_LOGIC.md §4, §6, §7, §8).

`calculate_spa` evaluates every line in the documented total order; the first failing gate sets the
exception code and every gate evaluated is traced. Pure: no I/O, clock or randomness.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext

from rebate_engine.adapters import AdapterInput, get_adapter
from rebate_engine.deadlines import deadline
from rebate_engine.models import (
    Code,
    EngineRuleError,
    LineResult,
    ManufacturerSettings,
    RoundingRule,
    SalesLine,
    SpaItem,
    SpaRunInput,
    SpaVersion,
    Status,
)
from rebate_engine.money import MONEY_CONTEXT
from rebate_engine.rounding import allocate_document, round_line
from rebate_engine.trace import Trace, trace_hash
from rebate_engine.uom import find_factor

ZERO = Decimal(0)
PARTIALLY_ELIGIBLE = "partially_eligible"
CANNOT_BE_NET_CLAIMED = "cannot_be_net_claimed"


def processing_order(line: SalesLine) -> tuple[date, str, int, str]:
    """Total order of §1.5: (invoice_date, document_number, line_number, id)."""
    return (line.invoice_date, line.document_number, line.line_number, line.line_id)


def normalise_reference(value: str) -> str:
    """Case- and space-insensitive comparison form of an SPA reference (§3.1)."""
    return "".join(value.split()).upper()


@dataclass(slots=True)
class _Eval:
    """Mutable per-line working state; frozen into a LineResult at the end."""

    line: SalesLine
    trace: Trace
    status: Status = Status.EXCEPTION
    code: Code | None = None
    version: SpaVersion | None = None
    unit: Decimal | None = None
    eligible_qty: Decimal | None = None
    excess_qty: Decimal | None = None
    claim_uom: str | None = None
    flags: list[str] = field(default_factory=list)
    raw: Decimal | None = None  # full-precision amount still to be rounded
    rounding: RoundingRule | None = None
    amount: Decimal = ZERO
    adjusted_version_id: str | None = None  # return adjustments: the claimed line's version

    def fail(self, code: Code, status: Status = Status.EXCEPTION) -> _Eval:
        self.status, self.code = status, code
        return self


def _line_date(line: SalesLine, basis: str, trace: Trace, version_id: str | None = None) -> date:
    if basis == "ship_date":
        if line.ship_date is not None:
            return line.ship_date
        trace.warn(
            f"ship_date_missing_fallback_invoice_date:{version_id}" if version_id else "ship_date_fallback"
        )
    return line.invoice_date


def _customer_in_scope(version: SpaVersion, line: SalesLine) -> bool:
    """Listed: the customer or an ancestor is included and none of them is excluded (exclusion wins)."""
    chain = {line.customer_id, *line.customer_ancestor_ids}
    scopes = {c.scope for c in version.customers if c.customer_id in chain}
    if "exclude" in scopes:
        return False
    return version.rules.customer_scope_mode == "all" or "include" in scopes


def _item(version: SpaVersion, product_id: str) -> SpaItem | None:
    return next((i for i in version.items if i.product_id == product_id), None)


def _select_version(
    ev: _Eval, versions: tuple[SpaVersion, ...], settings: ManufacturerSettings | None
) -> SpaVersion | None:
    line, t = ev.line, ev.trace
    product_id = line.product_id
    if product_id is None:
        t.step("product", known=False)
        ev.fail(Code.UNKNOWN_PRODUCT)
        return None
    mfr = sorted(
        (v for v in versions if v.manufacturer_id == line.manufacturer_id), key=lambda v: v.version_id
    )
    t.step("candidate_versions", found=[v.version_id for v in mfr])
    if not mfr:
        ev.fail(Code.NO_AGREEMENT)
        return None
    by_customer = [v for v in mfr if _customer_in_scope(v, line)]
    t.step("customer_scope", customer_id=line.customer_id, remaining=[v.version_id for v in by_customer])
    if not by_customer:
        ev.fail(Code.INELIGIBLE_CUSTOMER)
        return None
    by_product = [v for v in by_customer if _item(v, product_id) is not None]
    t.step("product_scope", product_id=product_id, remaining=[v.version_id for v in by_product])
    if not by_product:
        ev.fail(Code.INELIGIBLE_PRODUCT)
        return None
    in_range: list[SpaVersion] = []
    before_start = True
    for v in by_product:
        basis = v.rules.date_basis
        fallback = basis == "ship_date" and line.ship_date is None
        d = _line_date(line, basis, t, v.version_id)
        ok = v.valid_from <= d <= v.valid_to
        t.step(
            "date_check",
            version_id=v.version_id,
            basis=basis,
            value=d,
            range=[v.valid_from, v.valid_to],
            fallback="invoice_date" if fallback else None,
            **{"pass": ok},
        )
        before_start = before_start and d < v.valid_from
        if ok:
            in_range.append(v)
    if not in_range:
        ev.fail(Code.AGREEMENT_NOT_STARTED if before_start else Code.EXPIRED_AGREEMENT)
        return None
    if len(in_range) == 1:
        return in_range[0]
    policy = settings.conflict_policy if settings else "flag"
    top = max(v.priority for v in in_range)
    winners = [v for v in in_range if v.priority == top] if policy == "priority" else in_range
    winner = winners[0] if len(winners) == 1 else None
    t.step(
        "conflict",
        policy=policy,
        candidates=[{"version_id": v.version_id, "priority": v.priority} for v in in_range],
        winner=winner.version_id if winner else None,
    )
    if winner is None:
        ev.fail(Code.CONFLICTING_AGREEMENTS)
    return winner


def _reference_cost(ev: _Eval, version: SpaVersion, item: SpaItem) -> tuple[bool, Decimal | None]:
    """(ok, cost in its own unit). Agreement costs are per claim UoM; line costs per line UoM."""
    rules, line = version.rules, ev.line
    needs_ref = rules.basis in ("price_difference", "percent_of_cost", "guaranteed_margin")
    cost: Decimal | None = None
    if needs_ref:
        source = rules.cost_source
        cost = {
            "agreement_reference": item.reference_cost,
            "line_cost": line.unit_cost,
            "replacement_cost": line.replacement_cost,
        }[source]
        if cost is None:
            ev.trace.step("cost", source=source, reference_cost=None)
            ev.fail(Code.MISSING_REPLACEMENT_COST if source == "replacement_cost" else Code.MISSING_COST)
            return False, None
    if rules.basis == "price_difference" and item.deviated_cost is None:
        ev.trace.step("cost", source=rules.cost_source, reference_cost=cost, deviated_cost=None)
        ev.fail(Code.MISSING_COST)
        return False, None
    ev.trace.step("cost", source=rules.cost_source if needs_ref else None, reference_cost=cost)
    return True, cost


def _required(value: Decimal | None, name: str, version: SpaVersion) -> Decimal:
    if value is None:  # the AgreementValidator rejects such versions; never default
        raise EngineRuleError(f"{version.version_id}: item {name} required for basis {version.rules.basis}")
    return value


def _unit_amount(version: SpaVersion, item: SpaItem, c_ref: Decimal | None, price: Decimal) -> Decimal:
    basis = version.rules.basis
    if basis == "price_difference":
        return _required(c_ref, "reference_cost", version) - _required(
            item.deviated_cost, "deviated_cost", version
        )
    if basis == "percent_of_sell":
        return price * _required(item.rate, "rate", version)
    if basis == "percent_of_cost":
        return _required(c_ref, "reference_cost", version) * _required(item.rate, "rate", version)
    if basis == "flat_per_unit":
        return _required(item.flat_amount, "flat_amount", version)
    # guaranteed_margin: margin = rate
    margin = _required(item.rate, "rate", version)
    return _required(c_ref, "reference_cost", version) - price * (1 - margin)


def _claim_window(ev: _Eval, settings: ManufacturerSettings | None, run_date: date) -> bool:
    """True when inside the window (or the gate is skipped)."""
    if settings is None or settings.claim_window_basis is None or settings.claim_window_days is None:
        ev.trace.warn("claim_window_settings_missing")
        ev.trace.step("claim_window", skipped=True)
        return True
    basis = settings.claim_window_basis
    if basis not in ("invoice_date", "ship_date"):
        # `period_end` is a DB enum value with no definition in BUSINESS_LOGIC.md §4.0/§12: refuse to guess.
        raise EngineRuleError(f"claim_window_basis {basis!r} is not defined in BUSINESS_LOGIC.md")
    due = deadline(_line_date(ev.line, basis, ev.trace), settings.claim_window_days)
    inside = run_date <= due
    ev.trace.step(
        "claim_window",
        basis=basis,
        days=settings.claim_window_days,
        deadline=due,
        run_date=run_date,
        **{"pass": inside},
    )
    return inside


def _evaluate_original(
    ev: _Eval,
    run: SpaRunInput,
    settings: ManufacturerSettings | None,
    consumed: dict[tuple[str, str], Decimal],
) -> None:
    line, t = ev.line, ev.trace
    version = _select_version(ev, run.versions, settings)
    if version is None:
        return
    ev.version = version
    rules = version.rules
    assert line.product_id is not None  # noqa: S101 - guaranteed by _select_version
    item = _item(version, line.product_id)
    assert item is not None  # noqa: S101

    # SPA reference
    ref = line.spa_reference.strip() if line.spa_reference else ""
    matches = bool(ref) and normalise_reference(ref) == normalise_reference(version.external_number)
    t.step(
        "spa_reference",
        required=rules.spa_reference_required,
        value=ref or None,
        expected=version.external_number,
        **{"pass": matches or not rules.spa_reference_required},
    )
    if rules.spa_reference_required:
        if not ref:
            ev.fail(Code.MISSING_SPA_REFERENCE)
            return
        if not matches:
            ev.fail(Code.SPA_REFERENCE_MISMATCH)
            return
    elif ref and not matches:
        t.warn("spa_reference_differs")

    # Costs, then UoM
    ok, cost = _reference_cost(ev, version, item)
    if not ok:
        return
    claim_uom = item.claim_uom or rules.claim_uom or line.base_uom
    if claim_uom is None:
        raise EngineRuleError(f"line {line.line_id}: product base UoM unknown and no claim UoM set")
    ev.claim_uom = claim_uom
    factor = find_factor(run.conversions, line.product_id, line.uom, claim_uom)
    t.step("uom", line_uom=line.uom, claim_uom=claim_uom, factor=factor)
    if factor is None:
        ev.fail(Code.MISSING_UOM_CONVERSION)
        return
    if factor <= 0:
        raise EngineRuleError(f"non-positive UoM factor {line.uom}->{claim_uom}")
    price = line.unit_price / factor  # per claim UoM
    c_ref = cost if rules.cost_source == "agreement_reference" or cost is None else cost / factor

    # Price check
    if rules.price_check_mode == "flag" and item.sell_price is not None:
        diff = abs(price - item.sell_price)
        ok = diff <= rules.price_tolerance
        t.step(
            "price_check",
            mode="flag",
            unit_price=price,
            sell_price=item.sell_price,
            difference=diff,
            tolerance=rules.price_tolerance,
            **{"pass": ok},
        )
        if not ok:
            ev.fail(Code.PRICE_MISMATCH)
            return
    else:
        t.step("price_check", mode=rules.price_check_mode, sell_price=item.sell_price, skipped=True)

    # Returns netting (§7): unclaimed originals are computed net; claimed originals stay on the frozen basis
    claimed = line.prior_claim is not None
    net = line.quantity if claimed else line.quantity - line.returned_quantity
    t.step(
        "returns", quantity=line.quantity, returned_quantity=line.returned_quantity, claimed=claimed, net=net
    )
    q = net * factor
    if q <= 0:
        t.step("calc", eligible_qty=ZERO, note="fully_returned")
        ev.fail(Code.NO_CLAIM_DUE, Status.NOT_APPLICABLE)
        return

    # Quantity cap (§4.2)
    eligible = q
    cap_key = (version.agreement_id, line.product_id)
    if item.max_quantity is not None and rules.cap_scope != "none":
        if rules.cap_scope == "per_agreement":
            # Pool size for per_agreement is not defined in BUSINESS_LOGIC.md §4.2: refuse to guess.
            raise EngineRuleError("cap_scope per_agreement: pooled max_quantity is not defined")
        before = consumed.get(cap_key, ZERO)
        remaining = item.max_quantity - before
        eligible = min(q, max(remaining, ZERO))
        t.step(
            "cap",
            scope=rules.cap_scope,
            max_quantity=item.max_quantity,
            consumed_before=before,
            remaining=remaining,
            eligible_qty=eligible,
        )
        if remaining <= 0:
            ev.fail(Code.CAP_EXHAUSTED)
            return
        if eligible < q:
            ev.flags.append(PARTIALLY_ELIGIBLE)
            ev.excess_qty = q - eligible
    ev.eligible_qty = eligible

    # Amount (§4.1)
    if rules.adapter is not None:
        u = get_adapter(rules.adapter)(
            AdapterInput(
                line=line,
                item=item,
                claim_uom=claim_uom,
                factor=factor,
                reference_cost=c_ref,
                unit_price=price,
            )
        )
    else:
        u = _unit_amount(version, item, c_ref, price)
    ev.unit = u
    raw = eligible * u
    t.step(
        "calc",
        basis=rules.basis,
        adapter=rules.adapter,
        reference_cost=c_ref,
        deviated_cost=item.deviated_cost,
        rate=item.rate,
        flat_amount=item.flat_amount,
        unit_price=price,
        unit_amount=u,
        eligible_qty=eligible,
        raw=raw,
    )
    if u == 0:
        ev.fail(Code.NO_CLAIM_DUE, Status.NOT_APPLICABLE)
        return
    if u < 0:
        ev.fail(Code.NEGATIVE_CLAIM_AMOUNT)
        return
    ev.raw, ev.rounding = raw, rules.rounding

    if not _claim_window(ev, settings, run.run_date):
        ev.fail(Code.OUTSIDE_CLAIM_WINDOW)  # amount still stored (money at risk)
        return
    ev.status = Status.ELIGIBLE
    if not claimed:
        consumed[cap_key] = consumed.get(cap_key, ZERO) + eligible


def _evaluate_return(ev: _Eval, run: SpaRunInput, settings: ManufacturerSettings | None) -> None:
    line, t = ev.line, ev.trace
    orig = line.return_original
    if orig is None:
        t.step("return", original=None)
        ev.fail(Code.RETURN_ORIGINAL_MISSING)
        return
    t.step(
        "return",
        original=orig.line_id,
        original_quantity=orig.quantity,
        returned_quantity=orig.returned_quantity,
        original_claimed=orig.prior_claim is not None,
    )
    if orig.returned_quantity > orig.quantity:
        ev.fail(Code.RETURN_EXCEEDS_ORIGINAL)
        return
    pc = orig.prior_claim
    if pc is None:
        ev.status = Status.NETTED
        return
    factor = find_factor(run.conversions, line.product_id, line.uom, pc.claim_uom)
    t.step("uom", line_uom=line.uom, claim_uom=pc.claim_uom, factor=factor)
    if factor is None:
        ev.fail(Code.MISSING_UOM_CONVERSION)
        return
    qty = abs(line.quantity) * factor
    raw = -(pc.unit_amount * qty)
    ev.claim_uom, ev.unit, ev.eligible_qty = pc.claim_uom, pc.unit_amount, -qty
    ev.status, ev.amount = Status.ADJUSTMENT, round_line(raw, pc.rounding)
    ev.adjusted_version_id = pc.agreement_version_id
    t.step(
        "adjustment",
        agreement_version_id=pc.agreement_version_id,
        unit_amount=pc.unit_amount,
        returned_qty=qty,
        raw=raw,
    )
    t.step("round", mode=pc.rounding.mode, scale=pc.rounding.scale, timing="per_line", result=ev.amount)
    if not (settings and settings.allow_negative_claims):
        ev.flags.append(CANNOT_BE_NET_CLAIMED)


def calculate_spa(run: SpaRunInput) -> tuple[LineResult, ...]:
    """Evaluate every line; results come back in processing order (§1.5)."""
    with localcontext(MONEY_CONTEXT):
        settings = {m.manufacturer_id: m for m in run.manufacturers}
        consumed: dict[tuple[str, str], Decimal] = dict(run.prior_consumption)
        evals: list[_Eval] = []
        for line in sorted(run.lines, key=processing_order):
            ev = _Eval(
                line=line,
                trace=Trace(
                    "line",
                    {
                        "id": line.line_id,
                        "document_number": line.document_number,
                        "line_number": line.line_number,
                        "invoice_date": line.invoice_date,
                        "ship_date": line.ship_date,
                        "customer_id": line.customer_id,
                        "product_id": line.product_id,
                        "quantity": line.quantity,
                        "uom": line.uom,
                        "unit_price": line.unit_price,
                        "unit_cost": line.unit_cost,
                        "replacement_cost": line.replacement_cost,
                        "spa_reference": line.spa_reference,
                    },
                ),
            )
            mfr_settings = settings.get(line.manufacturer_id) if line.manufacturer_id else None
            pc = line.prior_claim
            if line.quantity < 0:
                _evaluate_return(ev, run, mfr_settings)
            else:
                _evaluate_original(ev, run, mfr_settings, consumed)
                if pc is not None and line.product_id is not None:
                    # a claimed line consumes what it claimed, less returns (§4.2 "returns restore consumption")
                    key = (pc.agreement_id, line.product_id)
                    factor = find_factor(run.conversions, line.product_id, line.uom, pc.claim_uom)
                    returned = line.returned_quantity * factor if factor is not None else ZERO
                    consumed[key] = consumed.get(key, ZERO) + max(pc.quantity - returned, ZERO)
            evals.append(ev)

        _round(evals)
        return tuple(_freeze(ev) for ev in evals)


def _round(evals: list[_Eval]) -> None:
    documents: dict[tuple[str, str], list[_Eval]] = defaultdict(list)
    for ev in evals:
        if ev.raw is None or ev.rounding is None or ev.version is None:
            continue
        if ev.rounding.timing == "per_document":
            documents[(ev.line.document_number, ev.version.version_id)].append(ev)
        else:
            ev.amount = round_line(ev.raw, ev.rounding)
            ev.trace.step(
                "round", mode=ev.rounding.mode, scale=ev.rounding.scale, timing="per_line", result=ev.amount
            )
    for group in documents.values():
        rule = group[0].rounding
        assert rule is not None  # noqa: S101
        amounts = allocate_document([ev.raw for ev in group if ev.raw is not None], rule)
        total = sum(amounts, ZERO)
        for ev, amount in zip(group, amounts, strict=True):
            ev.amount = amount
            ev.trace.step(
                "round",
                mode=rule.mode,
                scale=rule.scale,
                timing="per_document",
                document_lines=len(group),
                document_total=total,
                result=amount,
            )


def _freeze(ev: _Eval) -> LineResult:
    trace = ev.trace.build(
        ev.version.rules.schema_version if ev.version else None,
        {
            "status": ev.status.value,
            "claim_amount": ev.amount,
            "exception": ev.code.value if ev.code else None,
            "flags": list(ev.flags),
        },
    )
    return LineResult(
        line_id=ev.line.line_id,
        status=ev.status,
        exception_code=ev.code,
        agreement_version_id=ev.version.version_id if ev.version else ev.adjusted_version_id,
        claim_amount=ev.amount,
        claim_unit_amount=ev.unit,
        eligible_quantity=ev.eligible_qty,
        excess_quantity=ev.excess_qty,
        claim_uom=ev.claim_uom,
        flags=tuple(ev.flags),
        trace=trace,
        inputs_hash=trace_hash(trace),
    )


def propose_recalculation(frozen_amount: Decimal, new: LineResult) -> Decimal | None:
    """§9.3: adjustment amount for a line with a frozen claim, or None when no action is needed."""
    with localcontext(MONEY_CONTEXT):
        if new.status != Status.ELIGIBLE:
            return -frozen_amount
        diff = new.claim_amount - frozen_amount
        return None if diff == 0 else diff
