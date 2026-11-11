"""Run orchestration (S5.3): pinned inputs -> engine -> chunked append-only persistence -> totals and checksums.

Order inside a run (BUSINESS_LOGIC.md §5.4): per manufacturer the SPA calculation first (its eligible lines are
the second SPA-line signal), then the rebate agreements. One run per tenant executes at a time (advisory lock);
a failed run keeps its already committed rows but readers only see ``completed`` runs (partial runs are invisible).

Memory: one engine call per manufacturer holds that manufacturer's lines and traces in memory. Measured numbers
for the 1M-line baseline are in ``docs/reports/notes/c1-s5-runs.md``.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.db import transaction
from rebate.core.errors import Conflict, ValidationFailed
from rebate.core.jobs import PermanentError
from rebate.modules import audit
from rebate.modules.claims import frozen, loaders, persistence
from rebate.modules.claims.models import CalculationRun
from rebate_engine.deadlines import rebate_deadline
from rebate_engine.models import (
    EngineRuleError,
    ManufacturerSettings,
    SpaRunInput,
    Status,
)
from rebate_engine.rebate import calculate_accrual
from rebate_engine.spa import calculate_spa
from rebate_engine.trace import ENGINE_VERSION

LOCK_KEY = "calc_run:{tenant}"


class RunInProgress(Conflict):
    code = "run_in_progress"
    message = "A calculation run is already executing for this workspace."


class RunRuleError(ValidationFailed):
    """The scope cannot be calculated because an agreement or setting uses rules the engine refuses."""


@dataclass(frozen=True)
class Scope:
    manufacturer_ids: tuple[UUID, ...]  # empty = every manufacturer (and lines without a known manufacturer)
    date_from: date
    date_to: date

    def as_json(self) -> dict[str, Any]:
        return {
            "manufacturer_ids": sorted(str(m) for m in self.manufacturer_ids),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Scope:
        return cls(
            tuple(UUID(m) for m in data["manufacturer_ids"]),
            date.fromisoformat(data["date_from"]),
            date.fromisoformat(data["date_to"]),
        )


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def detail(field_: str, code: str, message: str) -> dict[str, str]:
    return {"field": field_, "code": code, "message": message}


def error_detail(external_number: str, exc: EngineRuleError) -> dict[str, str]:
    where = getattr(exc, "field", None)
    return detail(
        f"agreement:{external_number}" + (f".{where}" if where else ""),
        getattr(exc, "code", "engine_rejected"),
        str(exc),
    )


def preflight(
    db: Session, tenant_id: UUID, scope: Scope, as_of: datetime
) -> tuple[loaders.Versions, tuple[ManufacturerSettings, ...]]:
    """Everything that makes a run un-runnable, reported before any work: unsupported rules in a stored version,
    ``claim_window_basis = period_end`` for a manufacturer with SPA agreements (rebates only), and a sales-basis
    rebate whose measurement period starts before the run window (the SPA-line signal would be incomplete)."""
    mfrs = list(scope.manufacturer_ids)
    try:
        versions = loaders.load_versions(db, tenant_id, as_of, mfrs)
    except loaders.VersionRejected as exc:
        raise RunRuleError(details=[error_detail(exc.external_number, exc.error)]) from exc
    settings = loaders.load_settings(db, tenant_id, mfrs)
    problems: list[dict[str, str]] = []
    spa_makers = {v.manufacturer_id for v in versions.spa}
    for s in settings:
        if s.claim_window_basis == "period_end" and s.manufacturer_id in spa_makers:
            problems.append(
                detail(
                    f"manufacturer_settings:{s.manufacturer_id}.claim_window_basis",
                    "engine_rejected",
                    "claim_window_basis 'period_end' applies to rebates only; use 'month_end' for SPA lines.",
                )
            )
    for entry in versions.rebates:
        rules = entry.version.rules
        if (
            rules.volume_basis == "sales"
            and rules.spa_lines != "include"
            and rules.period_start < scope.date_from
        ):
            problems.append(
                detail(
                    f"agreement:{entry.version.external_number}",
                    "scope_too_narrow",
                    "The run must start on or before the rebate's period start (spa_lines needs the SPA results "
                    "of every sales line in the period).",
                )
            )
    if problems:
        raise RunRuleError(details=problems)
    return versions, settings


@dataclass
class Totals:
    lines: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    by_exception: Counter[str] = field(default_factory=Counter)
    eligible_amount: Decimal = Decimal(0)
    at_risk_amount: Decimal = Decimal(0)  # outside_claim_window: money lost to expiry
    adjustment_amount: Decimal = Decimal(0)
    accrual_entries: int = 0
    accrual_delta: Decimal = Decimal(0)
    result_hashes: list[tuple[str, str]] = field(default_factory=list)
    checksum: Any = field(default_factory=hashlib.sha256)

    def feed(self, *parts: str) -> None:
        self.checksum.update("|".join(parts).encode() + b"\n")

    def as_json(self) -> dict[str, Any]:
        results_hash = hashlib.sha256()
        for pair in sorted(self.result_hashes):
            results_hash.update(":".join(pair).encode() + b"\n")
        return {
            "lines": self.lines,
            "by_status": dict(sorted(self.by_status.items())),
            "by_exception": dict(sorted(self.by_exception.items())),
            "eligible_amount": persistence.money(self.eligible_amount),
            "at_risk_amount": persistence.money(self.at_risk_amount),
            "adjustment_amount": persistence.money(self.adjustment_amount),
            "accrual_entries": self.accrual_entries,
            "accrual_delta": persistence.money(self.accrual_delta),
            "results_hash": results_hash.hexdigest(),
            "engine_version": ENGINE_VERSION,
        }


def config_hash(
    scope: Scope, as_of: datetime, settings: tuple[ManufacturerSettings, ...], conversions: tuple[Any, ...]
) -> str:
    return sha256(
        canonical(
            {
                "engine_version": ENGINE_VERSION,
                "scope": scope.as_json(),
                "as_of": as_of.isoformat(),
                "settings": [
                    [
                        s.manufacturer_id,
                        s.conflict_policy,
                        s.claim_window_basis,
                        s.claim_window_days,
                        s.allow_negative_claims,
                    ]
                    for s in settings
                ],
                "conversions": [[c.product_id, c.from_uom, c.to_uom, str(c.factor)] for c in conversions],
            }
        )
    )


def execute(
    engine: Engine,
    tenant_id: UUID,
    run_id: UUID,
    lock_db: Session,
    source: frozen.FrozenClaimSource | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Run ``run_id`` to completion. ``lock_db`` is a session whose open transaction holds the per-tenant lock
    for the whole run (the job's own session). Raises ``RunInProgress`` (retryable) when another run holds it,
    ``PermanentError`` after recording a failed run."""
    source = source or frozen.source
    got = lock_db.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": LOCK_KEY.format(tenant=tenant_id)},
    ).scalar_one()
    if not got:
        raise RunInProgress()
    with transaction(engine, tenant_id) as db:
        run = db.get(CalculationRun, run_id)
        if run is None:
            raise PermanentError("calculation run not found")
        if run.status != "queued":
            raise PermanentError(f"calculation run is {run.status}, not queued")
        scope = Scope.from_json(run.scope)
        as_of, cutoff, run_date = run.as_of, run.data_cutoff, run.run_date
        db.execute(
            update(CalculationRun)
            .where(CalculationRun.id == run_id)
            .values(status="running", started_at=clock.now())
        )
    try:
        totals, version_ids = _calculate(
            engine, tenant_id, run_id, scope, as_of, cutoff, run_date, source, heartbeat
        )
    except RunRuleError as exc:
        _fail(engine, tenant_id, run_id, exc.details)
        raise PermanentError(
            "calculation run rejected: " + "; ".join(d["message"] for d in exc.details)
        ) from exc
    except EngineRuleError as exc:
        _fail(
            engine, tenant_id, run_id, [detail("engine", getattr(exc, "code", "engine_rejected"), str(exc))]
        )
        raise PermanentError(f"calculation run rejected by the engine: {exc}") from exc
    except Exception:
        _fail(engine, tenant_id, run_id, [detail("run", "internal_error", "The run failed unexpectedly.")])
        raise
    with transaction(engine, tenant_id) as db:
        db.execute(
            update(CalculationRun)
            .where(CalculationRun.id == run_id)
            .values(
                status="completed",
                totals=totals.as_json(),
                input_checksum=totals.checksum.hexdigest(),
                agreement_version_ids=version_ids,
                completed_at=clock.now(),
            )
        )
        audit.record(
            db,
            tenant_id=tenant_id,
            actor=audit.Actor("system"),
            action=audit.Action.CALCULATION_RUN_COMPLETED,
            entity_type="calculation_run",
            entity_id=run_id,
            after={"status": "completed", "totals": totals.as_json()},
        )


def _fail(engine: Engine, tenant_id: UUID, run_id: UUID, details: list[dict[str, str]]) -> None:
    with transaction(engine, tenant_id) as db:
        db.execute(
            update(CalculationRun)
            .where(CalculationRun.id == run_id)
            .values(status="failed", error={"details": details}, completed_at=clock.now())
        )
        audit.record(
            db,
            tenant_id=tenant_id,
            actor=audit.Actor("system"),
            action=audit.Action.CALCULATION_RUN_FAILED,
            entity_type="calculation_run",
            entity_id=run_id,
            after={"status": "failed", "error": {"details": details}},
        )


def _calculate(
    engine: Engine,
    tenant_id: UUID,
    run_id: UUID,
    scope: Scope,
    as_of: datetime,
    cutoff: datetime,
    run_date: date,
    source: frozen.FrozenClaimSource,
    heartbeat: Callable[[], None] | None,
) -> tuple[Totals, list[UUID]]:
    totals = Totals()
    with transaction(engine, tenant_id) as db:
        versions, settings = preflight(db, tenant_id, scope, as_of)
        conversions = loaders.load_conversions(db, tenant_id)
        ancestors = loaders.customer_ancestors(db, tenant_id)
        everyone = loaders.load_manufacturer_ids(db, tenant_id)
    totals.feed("config", config_hash(scope, as_of, settings, conversions))
    for vid, content_hash in sorted(versions.spa_hashes.items()):
        totals.feed("version", vid, content_hash)
    for entry in versions.rebates:
        totals.feed("version", entry.version.version_id, entry.content_hash)

    groups: list[UUID | None] = [*sorted(scope.manufacturer_ids or everyone)]
    if not scope.manufacturer_ids:
        groups.append(None)  # lines whose product is unknown have no manufacturer: unknown_product results
    eligible_ids: set[str] = set()
    for group in groups:
        with transaction(engine, tenant_id) as db:
            batch = loaders.load_sales_lines(
                db, tenant_id, group, scope.date_from, scope.date_to, cutoff, ancestors, source
            )
            consumption = source.consumption(db, tenant_id, [UUID(ln.line_id) for ln in batch.lines])
        if not batch.lines:
            continue
        for line_id, row_hash in batch.checksum_rows:
            totals.feed("sales", line_id, row_hash)
        mine = str(group) if group else None
        results = calculate_spa(
            SpaRunInput(
                run_date=run_date,
                lines=batch.lines,
                versions=tuple(v for v in versions.spa if v.manufacturer_id == mine),
                manufacturers=settings,
                conversions=conversions,
                prior_consumption=dict(consumption),
            )
        )
        persistence.insert_results(engine, tenant_id, run_id, results, heartbeat)
        for r in results:
            totals.lines += 1
            totals.by_status[r.status.value] += 1
            if r.exception_code is not None:
                totals.by_exception[r.exception_code.value] += 1
            if r.status is Status.ELIGIBLE:
                totals.eligible_amount += r.claim_amount
                eligible_ids.add(r.line_id)
            elif r.exception_code is not None and r.exception_code.value == "outside_claim_window":
                totals.at_risk_amount += r.claim_amount
            elif r.status is Status.ADJUSTMENT:
                totals.adjustment_amount += r.claim_amount
            totals.result_hashes.append((r.line_id, r.inputs_hash))

    _rebates(engine, tenant_id, run_id, scope, cutoff, versions, settings, frozenset(eligible_ids), totals)
    return totals, versions.ids


def _rebates(
    engine: Engine,
    tenant_id: UUID,
    run_id: UUID,
    scope: Scope,
    cutoff: datetime,
    versions: loaders.Versions,
    settings: tuple[ManufacturerSettings, ...],
    eligible_ids: frozenset[str],
    totals: Totals,
) -> None:
    window_days = {s.manufacturer_id: s.claim_window_days for s in settings}
    chosen: dict[tuple[UUID, date], loaders.RebateEntry] = {}
    for entry in versions.rebates:
        rules = entry.version.rules
        if rules.period_start > scope.date_to or rules.period_end < scope.date_from:
            continue
        # a split-supersede (§9.2) leaves several versions for one agreement and period: the latest terms in force
        # at the as-of date govern the whole cumulative figure (the ledger is keyed by agreement + period)
        if entry.valid_from > scope.date_to or entry.valid_to < rules.period_start:
            continue
        key = (entry.agreement_id, rules.period_start)
        if key not in chosen or entry.valid_from > chosen[key].valid_from:
            chosen[key] = entry
    for (agreement_id, period_start), entry in sorted(
        chosen.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])
    ):
        version, rules = entry.version, entry.version.rules
        mfr = UUID(version.manufacturer_id)
        window_end = min(rules.period_end, scope.date_to)
        with transaction(engine, tenant_id) as db:
            if rules.volume_basis == "purchases":
                lines, rows = loaders.purchase_volume_lines(
                    db, tenant_id, mfr, rules.period_start, window_end, cutoff
                )
            else:
                lines, rows = loaders.sales_volume_lines(
                    db, tenant_id, mfr, rules.period_start, window_end, cutoff, eligible_ids
                )
            prior = loaders.prior_accrued(db, tenant_id, agreement_id, period_start)
        for line_id, row_hash in rows:
            totals.feed("volume", line_id, row_hash)
        result = calculate_accrual(version, lines, scope.date_to, prior)
        days = window_days.get(version.manufacturer_id)
        deadline = None if days is None else rebate_deadline(rules.period_end, days)
        persistence.insert_accrual(engine, tenant_id, run_id, agreement_id, result, deadline)
        totals.accrual_entries += 1
        totals.accrual_delta += result.delta
