"""S10.4 property test: the dashboard totals equal the sums of the underlying rows, on generated recovery cases.

Each Hypothesis example inserts generated ``variance_cases`` (through the real table, triggers and RLS, as ``rebate_app``)
into the ground-truth workspace in a transaction that is rolled back, refreshes the summary, and compares the dashboard
with (a) values derived here from the generated data (BUSINESS_LOGIC.md / ``recovery.reporting_reads`` definitions:
short-paid = claimed-but-unpaid case types; at-risk = live reclaimable cases with unknown deadline or deadline within
14 days; expiring = known deadline within 14 days) and (b) the drill-down rows and totals of every metric."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebate.core.db import set_tenant
from rebate.core.figures import Filter
from rebate.modules.reporting import service
from tests.conftest import DbUrls
from tests.integration.recovery_support import NOW, Story, build_story
from tests.support import fake_clock, make_app

D = Decimal
TODAY = NOW.date()
RECLAIMABLE = ("short_paid", "rejected", "unpaid")
TYPES = (*RECLAIMABLE, "overpaid", "unmatched_payment", "source_changed")
CLAIMED = D("10410.00")  # the seven submitted fixture lines (claim_batches_fixture.json)

case = st.tuples(
    st.decimals(min_value=D("0.01"), max_value=D("99999.99"), places=2),
    st.one_of(st.none(), st.integers(-40, 60)),  # deadline as days from today, or unknown
    st.integers(0, 3),  # month offset of the case's creation (April..July 2026)
)
cases = st.dictionaries(st.tuples(st.integers(0, 6), st.sampled_from(TYPES)), case, max_size=12)


@pytest.fixture(scope="module")
def world(migrated_db: DbUrls, app_engine: Engine) -> Iterator[tuple[Story, Engine]]:
    app: FastAPI = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield build_story(app, stack), app_engine


def _insert(
    db: Session, story: Story, lines: list[uuid.UUID], key: tuple[int, str], row: tuple[Any, ...]
) -> None:
    kind, (amount, offset, month) = key[1], row
    deadline = None if offset is None else TODAY + timedelta(days=offset)
    db.execute(
        text(
            "INSERT INTO variance_cases (tenant_id, id, manufacturer_id, claim_line_id, case_type, reason_code, "
            "variance_amount, deadline_date, deadline_basis, created_at) VALUES (:t, :id, :m, :c, :k, 'rejected_price_mismatch', "
            ":a, :d, CASE WHEN CAST(:d AS date) IS NULL THEN NULL ELSE 'remittance_date' END, :at)"
        ),
        {
            "t": story.tenant, "id": uuid.uuid4(), "m": story.manufacturer, "c": lines[key[0]], "k": kind,
            "a": amount, "d": deadline, "at": datetime(2026, 4 + month, 15, 12, tzinfo=NOW.tzinfo),
        },
    )  # fmt: skip


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(generated=cases)
def test_dashboard_totals_equal_the_underlying_rows(
    world: tuple[Story, Engine], generated: dict[Any, Any]
) -> None:
    story, engine = world
    lines = list(story.claim_lines.values())
    horizon = TODAY + timedelta(days=14)
    live = [(k, a, off) for (_, k), (a, off, _) in generated.items()]
    # hand-derived from the generated data
    expected = {
        "claimed": CLAIMED,
        "collected": D(0),
        "short_paid": sum((a for k, a, _ in live if k in RECLAIMABLE), D(0)),
        "recovered": D(0),
        "written_off": D(0),
        "recovered_after_write_off": D(0),
        "at_risk": sum(
            (
                a
                for k, a, off in live
                if k in RECLAIMABLE and (off is None or TODAY + timedelta(days=off) <= horizon)
            ),
            D(0),
        ),
        "expiring": sum(
            (a for k, a, off in live if k in RECLAIMABLE and off is not None and 0 <= off <= 14), D(0)
        ),
    }
    with Session(engine) as db:
        try:
            set_tenant(db, story.tenant)
            for key, row in generated.items():
                _insert(db, story, lines, key, row)
            service.refresh(db, story.tenant)
            body = service.dashboard(db, story.tenant, Filter())
            assert {m: D(v) for m, v in body["figures"].items()} == expected
            assert (
                sum((D(m["figures"]["short_paid"]) for m in body["months"]), D(0)) == expected["short_paid"]
            )
            for metric in service.METRICS:
                out = service.drilldown(db, story.tenant, metric, Filter(), None, 10_000)
                assert out["consistent"] and out["total"] == expected[metric], metric
                assert sum((r.amount for r in out["rows"]), D(0)) == expected[metric] == out["figure"], metric
        finally:
            db.rollback()


def test_the_property_test_data_was_rolled_back(world: tuple[Story, Engine]) -> None:
    story, engine = world
    with engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(story.tenant)})
        assert conn.execute(text("SELECT count(*) FROM variance_cases")).scalar_one() == 0
        conn.rollback()
