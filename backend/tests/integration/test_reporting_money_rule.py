"""S10 money rule (owner decisions 2026-11-13, D-N5/D-N6): recovered after write-off is its own figure, and a month is
the business month of the tenant (America/Chicago in the test workspace). Fresh CM-7781 workspace of S9."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.core.figures import Filter
from rebate.modules.reporting import service
from tests.integration import test_recovery_cases as base
from tests.integration.recovery_support import (
    act,
    case_by,
    confirm_payment,
    open_cases,
    remittance,
    seed_remittance,
)
from tests.integration.test_recovery_cases import Cases, Env, events

env, cases = base.env, base.cases
D = Decimal


def test_money_paid_on_a_reclaim_after_the_write_off_is_reported_and_the_case_stays_closed(
    env: Env, cases: Cases, admin_engine: Engine
) -> None:
    """Hand: CL-1 short_paid 150.00 -> reclaim (150.00) -> written off (written_off 150.00; the reclaim is retired).
    The manufacturer pays 150.00 on 2026-08-20 against that reclaim anyway: recovered stays 0.00, written_off stays
    150.00, recovered_after_write_off = 150.00 in August; the case is unchanged (closed cases are immutable)."""
    api, cid, s = cases.api, cases.price, cases.story
    adj = act(api, "post", f"/cases/{cid}/reclaim", cid).json()["adjustment_id"]
    assert act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "Give up"}).status_code == 200
    closed = case_by(api, cid)
    assert (closed["status"], D(closed["written_off_amount"])) == ("written_off", D("150.00"))

    doc, rl = seed_remittance(admin_engine, s, "CM-7792", date(2026, 8, 20), [D("150.00")], [None])
    confirm_payment(admin_engine, s, rl[0], adj, D("150.00"))
    open_cases(env.engine, s, remittance(doc, s.manufacturer, date(2026, 8, 20), ()))  # the recovery hook
    after = case_by(api, cid)
    assert (after["status"], D(after["recovered_amount"]), after["revision"]) == (
        "written_off",
        D(0),
        closed["revision"],
    )
    assert not [e for e in events(api, cid) if e["event_type"] == "amount_recovered"]

    with transaction(env.engine, s.tenant) as db:
        service.refresh(db, s.tenant)
        body = service.dashboard(db, s.tenant, Filter())
        aug = service.drilldown(
            db, s.tenant, "recovered_after_write_off", Filter(month=date(2026, 8, 1)), None, 50
        )
    f = body["figures"]
    assert (f["recovered"], f["written_off"], f["recovered_after_write_off"]) == (
        D(0),
        D("150.00"),
        D("150.00"),
    )
    assert {str(m["month"]): m["figures"]["recovered_after_write_off"] for m in body["months"]} == {
        "2026-07-01": D(0),
        "2026-08-01": D("150.00"),
    }
    (row,) = aug["rows"]
    assert (row.amount, row.trace_kind, str(row.trace_id)) == (D("150.00"), "variance_case", str(cid))
    assert aug["consistent"] and aug["total"] == D("150.00")


def test_a_case_created_in_the_evening_local_time_belongs_to_that_local_month(
    env: Env, cases: Cases, admin_engine: Engine
) -> None:
    """Hand: a copy of the 2,080.00 case is created 2026-07-31 23:30 America/Chicago (CDT, UTC-5) = 2026-08-01 04:30
    UTC. Its month is July, not August."""
    s, late = cases.story, uuid4()
    with (
        admin_engine.begin() as c
    ):  # the immutability trigger bars an UPDATE of created_at: insert a copy (without the claim line, which is live once)
        cols = [
            r[0]
            for r in c.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'variance_cases'")
            )
        ]
        sel = ", ".join(
            {"id": ":new", "claim_line_id": "NULL", "created_at": "'2026-08-01 04:30+00'::timestamptz"}.get(
                x, x
            )
            for x in cols
        )
        c.execute(
            text(
                f"INSERT INTO variance_cases ({', '.join(cols)}) SELECT {sel} FROM variance_cases WHERE id = :i"
            ),
            {"i": cases.quantity, "new": late},
        )
    with transaction(env.engine, s.tenant) as db:
        service.refresh(db, s.tenant)
        july = service.drilldown(db, s.tenant, "short_paid", Filter(month=date(2026, 7, 1)), None, 50)
        august = service.drilldown(db, s.tenant, "short_paid", Filter(month=date(2026, 8, 1)), None, 50)
    assert late in {r.trace_id for r in july["rows"]}
    assert not august["rows"] and august["total"] == D(0)
