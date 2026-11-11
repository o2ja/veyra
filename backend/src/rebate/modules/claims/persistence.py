"""Chunked, append-only persistence of calculation output (S5.3/S5.4).

Each chunk commits in its own short transaction, so a failure part-way leaves committed rows of a run that is
then marked ``failed``. Readers only ever see ``completed`` runs (see ``service``), so those rows are invisible;
the append-only triggers keep them as evidence.
"""

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, insert

from rebate.core import clock
from rebate.core.db import transaction
from rebate.core.ids import new_id
from rebate.modules.claims.models import CalculationResult, RebateAccrualEntry
from rebate_engine.models import AccrualResult, LineResult

CHUNK = 1000


def result_row(tenant_id: UUID, run_id: UUID, r: LineResult) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": new_id(),
        "run_id": run_id,
        "sales_document_line_id": UUID(r.line_id),
        "agreement_version_id": None if r.agreement_version_id is None else UUID(r.agreement_version_id),
        "status": r.status.value,
        "exception_code": None if r.exception_code is None else r.exception_code.value,
        "eligible_quantity": r.eligible_quantity,
        "excess_quantity": r.excess_quantity,
        "claim_uom": r.claim_uom,
        "claim_unit_amount": r.claim_unit_amount,
        "claim_amount": r.claim_amount,
        "flags": list(r.flags),
        "trace": r.trace,
        "inputs_hash": r.inputs_hash,
        "created_at": clock.now(),
    }


def insert_results(
    engine: Engine,
    tenant_id: UUID,
    run_id: UUID,
    results: Sequence[LineResult],
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Insert in chunks; ``heartbeat`` (the job's lease renewal) runs after every committed chunk."""
    for i in range(0, len(results), CHUNK):
        rows = [result_row(tenant_id, run_id, r) for r in results[i : i + CHUNK]]
        with transaction(engine, tenant_id) as db:
            db.execute(insert(CalculationResult), rows)
        if heartbeat is not None:
            heartbeat()


def insert_accrual(
    engine: Engine,
    tenant_id: UUID,
    run_id: UUID,
    agreement_id: UUID,
    r: AccrualResult,
    claim_deadline: date | None,
) -> None:
    with transaction(engine, tenant_id) as db:
        db.execute(
            insert(RebateAccrualEntry).values(
                tenant_id=tenant_id,
                id=new_id(),
                run_id=run_id,
                agreement_id=agreement_id,
                agreement_version_id=UUID(r.agreement_version_id),
                period_start=r.period_start,
                period_end=r.period_end,
                as_of_date=r.as_of,
                cumulative_qualifying=r.qualifying_volume,
                cumulative_payout_base=r.payout_volume,
                tier_number=r.tier_number,
                tier_rate=r.rate,
                cumulative_earned=r.earned_cumulative,
                prior_cumulative_accrued=r.prior_cumulative_accrued,
                delta=r.delta,
                claim_deadline=claim_deadline,
                flags=list(r.flags),
                trace=r.trace,
                inputs_hash=r.inputs_hash,
                created_at=clock.now(),
            )
        )


def money(value: Decimal) -> str:
    return format(value, "f")
