"""Calculation runs: create/list/get, results, accrual ledger, tracker, recalculation proposals, draft preview.

Readers only ever see ``completed`` runs' output (partial-run invisibility, S5.3); the run row itself is visible
in every state so its progress and failure are.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import InvalidState, NotFound, ValidationFailed
from rebate.core.ids import new_id
from rebate.core.jobs import enqueue
from rebate.modules import agreements, audit, masterdata, tenancy
from rebate.modules.claims import frozen, loaders, runs
from rebate.modules.claims.models import CalculationRun
from rebate.modules.masterdata import Who, keyset
from rebate_engine.deadlines import rebate_deadline
from rebate_engine.models import EngineRuleError, LineResult, RebateVersion, SpaRunInput, Status, Tier
from rebate_engine.rebate import calculate_accrual
from rebate_engine.rules import RulesError, parse_rebate_rules, spa_version_from_payload
from rebate_engine.spa import calculate_spa
from rebate_engine.trace import ENGINE_VERSION

JOB_KIND = "calculation_run"


def _audit(db: Session, who: Who, action: audit.Action, run_id: UUID, **kw: Any) -> None:
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=action,
        entity_type="calculation_run",
        entity_id=run_id,
        meta=who.meta,
        **kw,
    )


def _business_date(db: Session, tenant_id: UUID, at: datetime) -> date:
    return at.astimezone(ZoneInfo(tenancy.get_settings(db, tenant_id).business_timezone)).date()


# ---- runs ---------------------------------------------------------------------------------------------------


def create_run(
    db: Session,
    who: Who,
    manufacturer_ids: list[UUID],
    date_from: date,
    date_to: date,
    as_of: datetime | None,
) -> tuple[CalculationRun, UUID]:
    now = clock.now()
    as_of = as_of or now
    if as_of > now:
        raise ValidationFailed(details=[runs.detail("as_of", "in_future", "as_of cannot be later than now.")])
    mfrs = sorted(set(manufacturer_ids))
    if mfrs:
        if masterdata.existing_manufacturer_ids(db, who.tenant_id, mfrs) != set(mfrs):
            raise ValidationFailed(
                details=[runs.detail("manufacturer_ids", "not_found", "Unknown manufacturer in the scope.")]
            )
    scope = runs.Scope(tuple(mfrs), date_from, date_to)
    _versions, settings = runs.preflight(db, who.tenant_id, scope, as_of)
    scope_hash = runs.sha256(runs.canonical(scope.as_json()))
    if db.scalar(
        text(
            "SELECT 1 FROM calculation_runs WHERE tenant_id = :t AND scope_hash = :h "
            "AND status IN ('queued', 'running')"
        ),
        {"t": who.tenant_id, "h": scope_hash},
    ):
        raise runs.RunInProgress("A run with the same scope is already queued or running.")
    run = CalculationRun(
        tenant_id=who.tenant_id,
        id=new_id(),
        scope=scope.as_json(),
        scope_hash=scope_hash,
        as_of=as_of,
        data_cutoff=now,
        run_date=_business_date(db, who.tenant_id, now),  # today: as_of only pins version visibility
        engine_version=ENGINE_VERSION,
        config_hash=runs.config_hash(scope, as_of, settings, loaders.load_conversions(db, who.tenant_id)),
        agreement_version_ids=[],
        status="queued",
        created_by=who.user_id,
        created_at=now,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:  # lost a race against an identical request: the unique index is the real guard
        raise runs.RunInProgress("A run with the same scope is already queued or running.") from None
    job_id = enqueue(
        db,
        JOB_KIND,
        tenant_id=who.tenant_id,
        payload={"run_id": str(run.id)},
        created_by=who.user_id,
        dedupe_key=f"{JOB_KIND}:{run.id}",
    )
    if job_id is None:  # unreachable: the dedupe key embeds a fresh id
        raise RuntimeError("calculation job was not enqueued")
    _audit(
        db,
        who,
        audit.Action.CALCULATION_RUN_CREATED,
        run.id,
        after={
            "scope": run.scope,
            "status": "queued",
            "as_of": as_of.isoformat(),
            "run_date": run.run_date.isoformat(),
            "engine_version": ENGINE_VERSION,
        },
    )
    return run, job_id


def get_run(db: Session, tenant_id: UUID, run_id: UUID) -> CalculationRun:
    run = db.scalar(
        select(CalculationRun).where(CalculationRun.tenant_id == tenant_id, CalculationRun.id == run_id)
    )
    if run is None:
        raise NotFound()
    return run


def completed_run(db: Session, tenant_id: UUID, run_id: UUID) -> CalculationRun:
    run = get_run(db, tenant_id, run_id)
    if run.status != "completed":
        raise InvalidState(f"The run is {run.status}; its output is available once it has completed.")
    return run


def list_runs(
    db: Session, tenant_id: UUID, *, status: str | None, limit: int, cursor: str | None
) -> list[CalculationRun]:
    stmt = select(CalculationRun).where(CalculationRun.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(CalculationRun.status == status)
    return list(db.scalars(keyset(stmt, CalculationRun.id, cursor, limit)))


# ---- results ------------------------------------------------------------------------------------------------

_RESULT_COLUMNS = (
    "r.id, r.run_id, r.sales_document_line_id, r.document_number, r.line_number, r.invoice_date, "
    "r.manufacturer_id, r.agreement_id, r.agreement_version_id, r.status, r.exception_code, "
    "r.eligible_quantity, r.excess_quantity, r.claim_uom, r.claim_unit_amount, r.claim_amount, r.flags, "
    "r.inputs_hash"
)
_RESULT_FROM = " FROM calculation_results r "


def list_results(
    db: Session,
    tenant_id: UUID,
    run_id: UUID,
    *,
    status: str | None,
    exception_code: str | None,
    manufacturer_id: UUID | None,
    agreement_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> list[Row[Any]]:
    completed_run(db, tenant_id, run_id)
    where = ["r.tenant_id = :t", "r.run_id = :run"]
    params: dict[str, Any] = {"t": tenant_id, "run": run_id, "n": limit + 1}
    for column, name, value in (
        ("r.status", "status", status),
        ("r.exception_code", "code", exception_code),
        ("r.manufacturer_id", "mfr", manufacturer_id),
        ("r.agreement_id", "agr", agreement_id),
    ):
        if value is not None:
            where.append(f"{column} = :{name}")
            params[name] = value
    after = masterdata.decode_cursor(cursor)
    if after is not None:
        where.append("r.id < :after")
        params["after"] = after
    sql = f"SELECT {_RESULT_COLUMNS}{_RESULT_FROM} WHERE {' AND '.join(where)} ORDER BY r.id DESC LIMIT :n"
    return list(db.execute(text(sql), params))


def get_result(db: Session, tenant_id: UUID, result_id: UUID) -> Row[Any]:
    row = db.execute(
        text(
            f"SELECT {_RESULT_COLUMNS}, r.trace{_RESULT_FROM} "
            "JOIN calculation_runs cr ON cr.tenant_id = r.tenant_id AND cr.id = r.run_id "
            "WHERE r.tenant_id = :t AND r.id = :id AND cr.status = 'completed'"
        ),
        {"t": tenant_id, "id": result_id},
    ).first()
    if row is None:
        raise NotFound()
    return row


# ---- ledger and tracker -------------------------------------------------------------------------------------

_ACCRUAL = (
    "SELECT e.id, e.run_id, e.agreement_id, e.agreement_version_id, e.external_number, e.agreement_name, "
    "e.period_start, e.period_end, e.as_of_date, e.cumulative_qualifying, e.cumulative_payout_base, e.tier_number, "
    "e.tier_rate, e.cumulative_earned, e.prior_cumulative_accrued, e.delta, e.claim_deadline, e.flags, e.created_at "
    "FROM rebate_accrual_entries e "
    "JOIN calculation_runs r ON r.tenant_id = e.tenant_id AND r.id = e.run_id AND r.status = 'completed' "
)


def list_accruals(
    db: Session,
    tenant_id: UUID,
    *,
    agreement_id: UUID | None,
    run_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> list[Row[Any]]:
    where = ["e.tenant_id = :t"]
    params: dict[str, Any] = {"t": tenant_id, "n": limit + 1}
    if agreement_id is not None:
        where.append("e.agreement_id = :agr")
        params["agr"] = agreement_id
    if run_id is not None:
        where.append("e.run_id = :run")
        params["run"] = run_id
    after = masterdata.decode_cursor(cursor)
    if after is not None:
        where.append("e.id < :after")
        params["after"] = after
    return list(
        db.execute(text(f"{_ACCRUAL}WHERE {' AND '.join(where)} ORDER BY e.id DESC LIMIT :n"), params)
    )


def tracker(db: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    """Per rebate agreement and period: the latest ledger entry of a completed run, with the tiers around it."""
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (e.agreement_id, e.period_start) e.run_id, e.agreement_id, e.agreement_version_id, "
            "e.external_number, e.agreement_name, e.manufacturer_id, e.tiers, e.period_start, e.period_end, "
            "e.as_of_date, e.cumulative_qualifying, e.cumulative_payout_base, e.tier_number, e.tier_rate, "
            "e.cumulative_earned, e.prior_cumulative_accrued + e.delta AS accrued_to_date, e.claim_deadline "
            "FROM rebate_accrual_entries e "
            "JOIN calculation_runs r ON r.tenant_id = e.tenant_id AND r.id = e.run_id AND r.status = 'completed' "
            "WHERE e.tenant_id = :t ORDER BY e.agreement_id, e.period_start, e.id DESC"
        ),
        {"t": tenant_id},
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        tiers = [
            {
                "tier_number": t["tier_number"],
                "lower_bound": Decimal(t["lower_bound"]),
                "rate": None if t["rate"] is None else Decimal(t["rate"]),
            }
            for t in sorted(r.tiers, key=lambda t: t["tier_number"])
        ]
        nxt = next((t for t in tiers if t["lower_bound"] > r.cumulative_qualifying), None)
        out.append(
            {
                **{k: v for k, v in r._mapping.items() if k != "tiers"},
                "tiers": tiers,
                "next_tier_lower_bound": None if nxt is None else nxt["lower_bound"],
                "amount_to_next_tier": None if nxt is None else nxt["lower_bound"] - r.cumulative_qualifying,
            }
        )
    return out


def audit_proposals(db: Session, who: Who, run_id: UUID, proposed: int) -> None:
    _audit(db, who, audit.Action.CALCULATION_ADJUSTMENTS_PROPOSED, run_id, after={"proposed": proposed})


# ---- draft preview ------------------------------------------------------------------------------------------


def preview(db: Session, who: Who, draft_id: UUID, limit: int) -> dict[str, Any]:
    """Run the engine on a draft's payload against up to ``limit`` imported lines; persists nothing."""
    draft = agreements.draft_for_preview(db, who.tenant_id, draft_id)
    if draft is None:
        raise NotFound()
    now = clock.now()
    today = _business_date(db, who.tenant_id, now)
    payload: dict[str, Any] = draft.payload
    shape = cast(
        Any, SimpleNamespace(agreement_id=draft.agreement_id, amends_version_id=draft.amends_version_id)
    )
    checked = agreements.validate(db, who.tenant_id, shape, payload, today)
    resolved = checked.resolved
    if not checked.valid or resolved is None:
        raise ValidationFailed(
            "The draft has validation errors; fix them before previewing.",
            details=[
                {"field": e["field"], "code": e["code"], "message": e["message"]} for e in checked.errors[:50]
            ],
        )
    mfr = resolved.manufacturer_id
    skus = {i["sku"]: str(pid) for i, pid in zip(payload.get("items", []), resolved.product_ids, strict=True)}
    settings = loaders.load_settings(db, who.tenant_id, [mfr])
    conversions = loaders.load_conversions(db, who.tenant_id)
    try:
        if payload["header"]["agreement_type"] == "spa":
            codes = {
                c["customer_code"]: str(cid)
                for c, (cid, _scope) in zip(payload.get("customers", []), resolved.customers, strict=True)
            }
            version = spa_version_from_payload(
                payload,
                version_id="draft",
                agreement_id="draft",
                manufacturer_id=str(mfr),
                product_ids=skus,
                customer_ids=codes,
            )
            batch = loaders.load_sales_lines(
                db,
                who.tenant_id,
                mfr,
                version.valid_from,
                version.valid_to,
                now,
                loaders.customer_ancestors(db, who.tenant_id),
                frozen.source,
                product_ids=[UUID(p) for p in skus.values()],
                limit=limit,
            )
            results = calculate_spa(
                SpaRunInput(
                    run_date=today,
                    lines=batch.lines,
                    versions=(version,),
                    manufacturers=settings,
                    conversions=conversions,
                )
            )
            return _spa_preview(db, who.tenant_id, today, batch, results)
        return _rebate_preview(db, who.tenant_id, payload, mfr, skus, settings, today, now)
    except EngineRuleError as exc:
        raise ValidationFailed(
            details=[runs.detail("payload", getattr(exc, "code", "engine_rejected"), str(exc))]
        ) from exc


def _spa_preview(
    db: Session, tenant_id: UUID, today: date, batch: loaders.SalesBatch, results: tuple[LineResult, ...]
) -> dict[str, Any]:
    meta = {ln.line_id: ln for ln in batch.lines}
    lines = [
        {
            "line_id": r.line_id,
            "document_number": meta[r.line_id].document_number,
            "line_number": meta[r.line_id].line_number,
            "invoice_date": meta[r.line_id].invoice_date,
            "status": r.status.value,
            "exception_code": None if r.exception_code is None else r.exception_code.value,
            "claim_amount": r.claim_amount,
            "claim_unit_amount": r.claim_unit_amount,
            "eligible_quantity": r.eligible_quantity,
            "flags": list(r.flags),
            "trace": r.trace,
        }
        for r in results
    ]
    eligible = sum((r.claim_amount for r in results if r.status is Status.ELIGIBLE), Decimal(0))
    return {
        "kind": "spa",
        "run_date": today,
        "sample_size": len(lines),
        "totals": {
            "eligible": sum(1 for r in results if r.status is Status.ELIGIBLE),
            "exceptions": sum(1 for r in results if r.status is Status.EXCEPTION),
            "eligible_amount": str(eligible),
        },
        "lines": lines,
        "accrual": None,
    }


def _rebate_preview(
    db: Session,
    tenant_id: UUID,
    payload: dict[str, Any],
    mfr: UUID,
    skus: dict[str, str],
    settings: tuple[Any, ...],
    today: date,
    now: datetime,
) -> dict[str, Any]:
    rules = parse_rebate_rules(payload["rules"])
    try:
        tiers = tuple(
            Tier(tier_number=t["tier_number"], lower_bound=Decimal(t["lower_bound"]), rate=Decimal(t["rate"]))
            for t in payload.get("tiers", [])
        )
    except (KeyError, ArithmeticError, TypeError) as exc:
        raise RulesError(f"tiers: {exc}") from exc
    version = RebateVersion(
        version_id="draft",
        external_number=payload["header"]["external_number"],
        manufacturer_id=str(mfr),
        rules=rules,
        tiers=tiers,
        product_ids=tuple(skus[i["sku"]] for i in payload.get("items", [])),
    )
    as_of = min(rules.period_end, today)
    if rules.volume_basis == "purchases":
        volume, _ = loaders.purchase_volume_lines(db, tenant_id, mfr, rules.period_start, as_of, now)
        note = "Cumulative volume of the period to date; the ledger is not touched."
    else:
        volume, _ = loaders.sales_volume_lines(
            db, tenant_id, mfr, rules.period_start, as_of, now, frozenset()
        )
        note = (
            "Cumulative volume of the period to date. SPA eligibility needs a calculation run, so only lines with "
            "an SPA reference count as SPA lines here; the ledger is not touched."
        )
    result = calculate_accrual(version, volume, as_of, Decimal(0))
    days = next((s.claim_window_days for s in settings if s.manufacturer_id == str(mfr)), None)
    return {
        "kind": "rebate",
        "run_date": today,
        "sample_size": len(volume),
        "totals": {"earned_cumulative": str(result.earned_cumulative)},
        "lines": [],
        "accrual": {
            "period_start": result.period_start,
            "period_end": result.period_end,
            "as_of_date": result.as_of,
            "cumulative_qualifying": result.qualifying_volume,
            "cumulative_payout_base": result.payout_volume,
            "tier_number": result.tier_number,
            "tier_rate": result.rate,
            "cumulative_earned": result.earned_cumulative,
            "claim_deadline": None if days is None else rebate_deadline(rules.period_end, days),
            "flags": list(result.flags),
            "trace": result.trace,
            "note": note,
        },
    }
