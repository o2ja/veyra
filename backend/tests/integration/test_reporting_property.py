"""S10.4 property test: the dashboard totals equal the sums of the underlying rows, on generated recovery cases.

Each Hypothesis example inserts generated ``variance_cases`` (through the real table, triggers and RLS, as ``rebate_app``)
into the ground-truth workspace in a transaction that is rolled back, refreshes the summary, and compares the dashboard
with (a) values derived here from the generated data (BUSINESS_LOGIC.md / ``recovery.reporting_reads`` definitions:
short-paid = claimed-but-unpaid case types; at-risk = live reclaimable cases with unknown deadline or deadline within
14 days; expiring = known deadline within 14 days) and (b) the drill-down rows and totals of every metric."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
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
CLAIMED_MONTH = date(2026, 7, 1)  # their batch is submitted in July 2026
LIVE = ("open", "to_reclaim", "submitted")
STATUSES = (*LIVE, "recovered", "written_off", "closed_no_action")
OTHER_MANUFACTURER = uuid.uuid4()

case = st.tuples(
    st.decimals(min_value=D("0.01"), max_value=D("99999.99"), places=2),
    st.one_of(st.none(), st.integers(-40, 60)),  # deadline as days from today, or unknown
    st.integers(0, 3),  # month offset of the case's creation (April..July 2026)
    st.sampled_from(STATUSES),
    st.integers(0, 100),  # percent of the variance recovered (a recovered case: all of it)
)
filters = st.tuples(  # manufacturer (None = all; the cases are the story's) and month (None = all)
    st.sampled_from((None, "story", "other")), st.one_of(st.none(), st.integers(2, 8))
)
cases = st.dictionaries(st.tuples(st.integers(0, 6), st.sampled_from(TYPES)), case, max_size=12)


@pytest.fixture(scope="module")
def world(migrated_db: DbUrls, app_engine: Engine) -> Iterator[tuple[Story, Engine]]:
    app: FastAPI = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield build_story(app, stack), app_engine


def _recovered(amount: Decimal, status: str, pct: int) -> Decimal:
    return amount if status == "recovered" else (amount * pct / 100).quantize(D("0.01"), ROUND_DOWN)


def _insert(
    db: Session, story: Story, lines: list[uuid.UUID], key: tuple[int, str], row: tuple[Any, ...]
) -> None:
    kind, (amount, offset, month, status, pct) = key[1], row
    recovered = _recovered(amount, status, pct)
    closed = status not in LIVE
    deadline = None if offset is None else TODAY + timedelta(days=offset)
    case_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO variance_cases (tenant_id, id, manufacturer_id, claim_line_id, case_type, reason_code, "
            "variance_amount, deadline_date, deadline_basis, created_at) VALUES (:t, :id, :m, :c, :k, 'rejected_price_mismatch', "
            ":a, :d, CASE WHEN CAST(:d AS date) IS NULL THEN NULL ELSE 'remittance_date' END, :at)"
        ),
        {
            "t": story.tenant, "id": case_id, "m": story.manufacturer, "c": lines[key[0]], "k": kind,
            "a": amount, "d": deadline, "at": datetime(2026, 4 + month, 15, 12, tzinfo=NOW.tzinfo),
        },
    )  # fmt: skip
    # a new case is open and untouched; the trigger allows only real transitions, so walk to the generated state
    path = {
        "open": ["open"], "to_reclaim": ["to_reclaim"], "submitted": ["to_reclaim", "submitted"],
        "recovered": ["to_reclaim", "submitted", "recovered"], "written_off": ["written_off"],
        "closed_no_action": ["closed_no_action"],
    }[status]  # fmt: skip
    for rev, step in enumerate(path, start=2):
        last = step == status
        db.execute(
            text(
                "UPDATE variance_cases SET status = :s, revision = :rev, "
                "recovered_amount = CASE WHEN :first OR :last THEN :r ELSE recovered_amount END, "
                "written_off_amount = :w, closed_at = CASE WHEN :closed THEN now() END, "
                "written_off_by = CASE WHEN :s = 'written_off' THEN CAST(:u AS uuid) END, "
                "written_off_at = CASE WHEN :s = 'written_off' THEN now() END "
                "WHERE tenant_id = :t AND id = :id"
            ),
            {
                "s": step, "rev": rev, "first": rev == 2, "last": last, "r": recovered, "closed": last and closed,
                "w": amount - recovered if step == "written_off" else D(0), "u": story.user,
                "t": story.tenant, "id": case_id,
            },
        )  # fmt: skip


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(generated=cases, flt=filters)
def test_dashboard_totals_equal_the_underlying_rows(
    world: tuple[Story, Engine], generated: dict[Any, Any], flt: tuple[str | None, int | None]
) -> None:
    story, engine = world
    lines = list(story.claim_lines.values())
    horizon = TODAY + timedelta(days=14)
    which, month_no = flt
    manufacturer = {None: None, "story": story.manufacturer, "other": OTHER_MANUFACTURER}[which]
    month = None if month_no is None else date(2026, month_no, 1)
    mine = manufacturer in (None, story.manufacturer)
    # (type, variance, deadline offset, status, recovered, written off) of the cases the filter lets through; the
    # cases were created on the 15th of April..July (offset 0..3)
    live = [
        (
            k,
            a,
            off,
            st_,
            _recovered(a, st_, pct),
            a - _recovered(a, st_, pct) if st_ == "written_off" else D(0),
        )
        for (_, k), (a, off, m, st_, pct) in generated.items()
        if mine and month in (None, date(2026, 4 + m, 1))
    ]
    is_live = [(k, a - rec, off) for k, a, off, st_, rec, _ in live if k in RECLAIMABLE and st_ in LIVE]
    # hand-derived from the generated data
    expected = {
        "claimed": CLAIMED if mine and month in (None, CLAIMED_MONTH) else D(0),
        "collected": D(0),
        "short_paid": sum((a for k, a, *_ in live if k in RECLAIMABLE), D(0)),
        "recovered": sum((rec for *_, rec, _ in live), D(0)),
        "written_off": sum((wo for *_, wo in live), D(0)),
        "recovered_after_write_off": D(0),
        "at_risk": sum(
            (rem for _, rem, off in is_live if off is None or TODAY + timedelta(days=off) <= horizon), D(0)
        ),
        "expiring": sum((rem for _, rem, off in is_live if off is not None and 0 <= off <= 14), D(0)),
    }
    flt_ = Filter(manufacturer, None, month)
    with Session(engine) as db:
        try:
            set_tenant(db, story.tenant)
            for key, row in generated.items():
                _insert(db, story, lines, key, row)
            service.refresh(db, story.tenant)
            body = service.dashboard(db, story.tenant, flt_)
            assert {m: D(v) for m, v in body["figures"].items()} == expected
            for metric, value in expected.items():  # the monthly rows add up to the figure
                assert sum((D(m["figures"][metric]) for m in body["months"]), D(0)) == value, metric
            for metric in service.METRICS:
                out = service.drilldown(db, story.tenant, metric, flt_, None, 10_000)
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
