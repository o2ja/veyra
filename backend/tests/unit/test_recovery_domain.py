"""Recovery pure domain (BUSINESS_LOGIC.md 12, 12.1). Expected values are hand-derived from the doc."""

import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rebate.modules.recovery.domain import deadlines as dl
from rebate.modules.recovery.domain import notifications as nt
from rebate.modules.recovery.domain.states import (
    ALLOWED,
    Actor,
    ApproverRequired,
    CaseStatus,
    FourEyesViolation,
    IllegalTransition,
    WriteOffNotProposed,
    apply_recovery,
    check_transition,
)
from rebate_engine.deadlines import reminder_dates

S = CaseStatus
D = date


def test_d1_claim_deadline_invoice_date() -> None:
    due = dl.spa_claim_deadline("invoice_date", D(2026, 3, 1), 60)
    assert due == D(2026, 4, 30)
    assert not dl.is_outside_window(due, D(2026, 4, 30))
    assert dl.is_outside_window(due, D(2026, 5, 2))
    assert dl.spa_claim_deadline("ship_date", D(2026, 3, 1), 60) == D(2026, 4, 30)


def test_d3_month_end_and_leap_year() -> None:
    due = dl.spa_claim_deadline("month_end", D(2026, 3, 10), 60)
    assert due == D(2026, 5, 30)
    assert not dl.is_outside_window(due, D(2026, 5, 30))
    assert dl.is_outside_window(due, D(2026, 5, 31))
    assert dl.spa_claim_deadline("month_end", D(2028, 2, 10), 60) == D(2028, 4, 29)
    assert dl.spa_claim_deadline("invoice_date", D(2028, 2, 28), 2) == D(2028, 3, 1)  # via 29 Feb
    assert dl.spa_claim_deadline("invoice_date", D(2026, 2, 28), 2) == D(2026, 3, 2)


def test_d4_rebate_period_end() -> None:
    assert dl.rebate_claim_deadline(D(2026, 12, 31), 60) == D(2027, 3, 1)


def test_d2_dispute_deadline_and_reminders() -> None:
    due = dl.dispute_deadline(D(2026, 5, 15), 60)
    assert due == D(2026, 7, 14)
    assert reminder_dates(due, nt.OFFSETS) == (D(2026, 6, 30), D(2026, 7, 7), D(2026, 7, 11))


def test_deadline_unknown_and_rejected_bases() -> None:
    assert dl.spa_claim_deadline(None, D(2026, 3, 1), 60) is None
    assert dl.spa_claim_deadline("invoice_date", None, 60) is None
    assert dl.spa_claim_deadline("invoice_date", D(2026, 3, 1), None) is None
    assert dl.rebate_claim_deadline(None, 60) is None
    assert dl.dispute_deadline(D(2026, 5, 15), None) is None
    assert dl.days_remaining(None, D(2026, 1, 1)) is None
    assert not dl.is_outside_window(None, D(2026, 1, 1))
    for bad in ("agreement_end", "period_end", "nonsense"):
        with pytest.raises(dl.RejectedWindowBasis):
            dl.spa_claim_deadline(bad, D(2026, 3, 1), 60)


def test_days_remaining_calendar_days_no_weekend_rolling() -> None:
    # 2026-07-11 is a Saturday: counted as a day, never rolled.
    assert dl.days_remaining(D(2026, 7, 14), D(2026, 7, 11)) == 3
    assert dl.days_remaining(D(2026, 7, 14), D(2026, 7, 14)) == 0
    assert dl.days_remaining(D(2026, 7, 14), D(2026, 7, 16)) == -2


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


NY = "America/New_York"


@pytest.mark.parametrize(
    ("instant", "zone", "expected"),
    [
        # US spring-forward 2026-03-08 02:00 EST -> EDT. Midnight EST = 05:00Z, midnight EDT = 04:00Z.
        (_utc(2026, 3, 7, 4, 59), NY, D(2026, 3, 6)),
        (_utc(2026, 3, 7, 5, 0), NY, D(2026, 3, 7)),
        (_utc(2026, 3, 8, 6, 59), NY, D(2026, 3, 8)),  # 01:59 EST
        (_utc(2026, 3, 9, 3, 59), NY, D(2026, 3, 8)),  # 23:59 EDT
        (_utc(2026, 3, 9, 4, 0), NY, D(2026, 3, 9)),  # 00:00 EDT
        # US autumn-back 2026-11-01 02:00 EDT -> EST.
        (_utc(2026, 11, 1, 3, 59), NY, D(2026, 10, 31)),  # 23:59 EDT
        (_utc(2026, 11, 1, 4, 0), NY, D(2026, 11, 1)),  # 00:00 EDT
        (_utc(2026, 11, 2, 4, 59), NY, D(2026, 11, 1)),  # 23:59 EST
        (_utc(2026, 11, 2, 5, 0), NY, D(2026, 11, 2)),  # 00:00 EST
        # Zone ahead of UTC: Sydney is +10 in May.
        (_utc(2026, 5, 14, 13, 59), "Australia/Sydney", D(2026, 5, 14)),
        (_utc(2026, 5, 14, 14, 0), "Australia/Sydney", D(2026, 5, 15)),
        (_utc(2026, 5, 14, 14, 0), "UTC", D(2026, 5, 14)),
    ],
)
def test_tenant_today(instant: datetime, zone: str, expected: date) -> None:
    assert dl.tenant_today(instant, zone) == expected


def test_tenant_today_requires_utc_aware() -> None:
    with pytest.raises(ValueError, match="UTC"):
        dl.tenant_today(datetime(2026, 1, 1), "UTC")
    with pytest.raises(ValueError, match="UTC"):
        dl.tenant_today(datetime.fromisoformat("2026-01-01T00:00:00+02:00"), "UTC")


dates = st.dates(min_value=D(2000, 1, 1), max_value=D(2100, 12, 31))


@given(dates, st.integers(0, 3650), st.integers(0, 3650))
def test_deadline_monotonic_and_exact(d: date, a: int, b: int) -> None:
    lo, hi = sorted((a, b))
    low, high = dl.spa_claim_deadline("invoice_date", d, lo), dl.spa_claim_deadline("invoice_date", d, hi)
    assert low is not None and high is not None
    assert low <= high
    assert high == d + timedelta(days=hi)  # never rolls past the formula date, whatever the weekday


@given(dates, st.integers(0, 3650))
def test_month_end_basis_properties(d: date, days: int) -> None:
    due = dl.spa_claim_deadline("month_end", d, days)
    assert due is not None
    month_end = (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    assert due == month_end + timedelta(days=days)
    assert due >= d


# --- state machine -----------------------------------------------------------------------------

EDGES = {
    (S.OPEN, S.TO_RECLAIM),
    (S.OPEN, S.CLOSED_NO_ACTION),
    (S.OPEN, S.WRITTEN_OFF),
    (S.TO_RECLAIM, S.SUBMITTED),
    (S.TO_RECLAIM, S.WRITTEN_OFF),
    (S.SUBMITTED, S.RECOVERED),
    (S.SUBMITTED, S.TO_RECLAIM),
    (S.SUBMITTED, S.WRITTEN_OFF),
}
USER, APPROVER, PROPOSER = Actor("u1", False), Actor("a1", True), Actor("p1", True)


@pytest.mark.parametrize(("cur", "tgt"), list(itertools.product(S, S)))
def test_every_pair(cur: CaseStatus, tgt: CaseStatus) -> None:
    if (cur, tgt) not in EDGES:
        with pytest.raises(IllegalTransition):
            check_transition(cur, tgt, APPROVER, write_off_proposed_by="p1")
    elif tgt is S.WRITTEN_OFF:
        check_transition(cur, tgt, APPROVER, write_off_proposed_by="p1")
    else:
        check_transition(cur, tgt, USER)


def test_allowed_table_matches_diagram() -> None:
    assert {(c, t) for c, ts in ALLOWED.items() for t in ts} == EDGES


def test_write_off_rules() -> None:
    with pytest.raises(ApproverRequired):
        check_transition(S.OPEN, S.WRITTEN_OFF, USER, write_off_proposed_by="p1")
    with pytest.raises(WriteOffNotProposed):
        check_transition(S.OPEN, S.WRITTEN_OFF, APPROVER)
    with pytest.raises(FourEyesViolation):
        check_transition(S.SUBMITTED, S.WRITTEN_OFF, PROPOSER, write_off_proposed_by="p1")
    check_transition(S.SUBMITTED, S.WRITTEN_OFF, APPROVER, write_off_proposed_by="p1")
    # workspace setting off: a single approver may write off, but a non-approver never may
    check_transition(S.OPEN, S.WRITTEN_OFF, PROPOSER, four_eyes=False)
    with pytest.raises(ApproverRequired):
        check_transition(S.OPEN, S.WRITTEN_OFF, USER, four_eyes=False)


def test_partial_then_full_recovery() -> None:
    amt = Decimal("150.00")
    assert apply_recovery(S.SUBMITTED, amt, Decimal(0), Decimal("100.00")) == (Decimal("100.00"), S.SUBMITTED)
    assert apply_recovery(S.SUBMITTED, amt, Decimal("100.00"), Decimal("50.00")) == (amt, S.RECOVERED)
    assert apply_recovery(S.SUBMITTED, amt, Decimal("100.00"), Decimal("49.99"))[1] is S.SUBMITTED
    for status in (S.OPEN, S.TO_RECLAIM, S.RECOVERED, S.WRITTEN_OFF, S.CLOSED_NO_ACTION):
        with pytest.raises(IllegalTransition):
            apply_recovery(status, amt, Decimal(0), Decimal(1))
    with pytest.raises(ValueError, match="positive"):
        apply_recovery(S.SUBMITTED, amt, Decimal(0), Decimal(0))


# --- notifications -----------------------------------------------------------------------------

DUE = D(2026, 7, 14)


@pytest.mark.parametrize(
    ("today", "offset"),
    [
        (D(2026, 6, 29), None),
        (D(2026, 6, 30), 14),
        (D(2026, 7, 6), 14),
        (D(2026, 7, 7), 7),
        (D(2026, 7, 10), 7),
        (D(2026, 7, 11), 3),
        (D(2026, 7, 12), 3),  # a case first seen late gets only the most urgent
        (D(2026, 7, 14), 3),  # the deadline day still reminds
        (D(2026, 7, 15), None),
    ],
)
def test_reminder_schedule(today: date, offset: int | None) -> None:
    r = nt.reminder_due("c1", DUE, today)
    assert (r.offset if r else None) == offset
    if r:
        assert r.dedupe_key == f"c1:deadline_reminder:{offset}"


def test_unknown_deadline_no_reminder_and_dedupe_stable() -> None:
    assert nt.reminder_due("c1", None, D(2026, 7, 1)) is None
    assert nt.reminder_due("c1", DUE, D(2026, 7, 8)) == nt.reminder_due("c1", DUE, D(2026, 7, 9))
    assert nt.reminder_due("c1", DUE, D(2026, 7, 8)) != nt.reminder_due("c2", DUE, D(2026, 7, 8))


def test_digest_grouping() -> None:
    a = nt.reminder_due("cA", D(2026, 7, 20), D(2026, 7, 17))
    b = nt.reminder_due("cB", D(2026, 7, 14), D(2026, 7, 11))
    assert a and b
    out = nt.group_digest([("u2", a), ("u1", a), ("u1", b), ("u1", a)])
    assert [d.recipient_id for d in out] == ["u1", "u2"]
    assert out[0].reminders == (b, a)  # soonest deadline first, duplicate collapsed
    assert out[1].reminders == (a,)


def test_domain_never_reads_the_clock() -> None:
    root = Path(__file__).resolve().parents[2] / "src/rebate/modules/recovery/domain"
    for f in root.glob("*.py"):
        text = f.read_text()
        assert not any(w in text for w in (".now(", ".today(", "utcnow", "time.time")), f.name
