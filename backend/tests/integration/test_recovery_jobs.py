"""S9.4 jobs against real Postgres and real Mailpit: the deadline sweep, notification dedupe, the email digest.

Hand values: the three dated cases (price 150.00, quantity 2,080.00, unmatched 75.00) fall due on 2026-09-29. The
reminders are 14, 7 and 3 days before: 2026-09-15, 2026-09-22, 2026-09-26; the deadline day itself repeats the 3-day
reminder, which is deduped. The unpaid case (660.00) falls due 2026-09-03 (submitted 07-05 + 60, D-N2); its reminders would be 08-20, 08-27
and 08-31, but the earliest sweep here is 2026-09-14, already past that deadline, so it never reminds. The recipients are the
tenant's active admins and approvers (two users) until a case has an owner. Tests run in file order."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text

from rebate.core.db import transaction
from rebate.core.jobs import enqueue
from rebate.modules.recovery import service
from tests.integration import test_recovery_cases as base
from tests.integration.recovery_support import NOW, act, case_by
from tests.integration.test_recovery_cases import Cases, Env
from tests.support import MAILPIT, Api, drain

env, cases = base.env, base.cases  # the same module-scoped fixtures, built afresh for this module


def at(env: Env, month: int, day: int) -> None:
    """Run the due jobs as of noon on that day, then put the clock back."""
    env.clock.t = datetime(2026, month, day, 12, 0, tzinfo=UTC)  # noon UTC: the same date in the tenant zone
    drain(env.app)
    env.clock.t = NOW  # the sessions were opened then and would be idle-expired two months later


def mine(api: Api) -> list[dict[str, Any]]:
    r = api.get("/notifications", params={"limit": 200})
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


def digests(address: str) -> list[dict[str, Any]]:
    """The digest mails to ``address`` (sending is synchronous inside the job, so no waiting)."""
    found = httpx.get(
        f"{MAILPIT}/api/v1/search",
        params={"query": f'to:"{address}" subject:"recovery deadline reminder"'},
        timeout=5,
    ).json()
    return [httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=5).json() for m in found["messages"]]


def email(api: Api) -> str:
    return str(api.get("/auth/me").json()["user"]["email"])


def test_no_reminder_before_the_first_offset(env: Env, cases: Cases) -> None:
    at(env, 9, 14)  # 15 days before 2026-09-29
    assert mine(cases.api) == [] and mine(cases.approver) == []
    assert digests(email(cases.api)) == []


def test_14_day_reminder_notifies_admins_and_approvers_and_mails_one_digest(env: Env, cases: Cases) -> None:
    at(env, 9, 15)
    for api in (cases.api, cases.approver):
        got = mine(api)
        assert len(got) == 3
        assert {n["entity_id"] for n in got} == {str(cases.price), str(cases.quantity), str(cases.unmatched)}
        assert all(n["kind"] == "deadline_reminder" and n["due_date"] == "2026-09-29" for n in got)
        assert all(n["read_at"] is None for n in got)
        assert any("150.00" in n["title"] and n["title"].endswith("deadline in 14 days") for n in got)
        assert any("2080.00" in n["title"] for n in got) and any("75.00" in n["title"] for n in got)
        assert not any(
            "660.00" in n["title"] for n in got
        )  # the unpaid case (due 2026-09-03) is past its deadline at every sweep date
    (m,) = digests(email(cases.api))
    assert m["Subject"] == "Veyra Recover: 3 recovery deadline reminder(s)"
    for amount in ("150.00", "2080.00", "75.00"):
        assert amount in m["Text"]
    assert "2026-09-29" in m["Text"] and "660.00" not in m["Text"]
    assert len(digests(email(cases.approver))) == 1


def test_a_second_sweep_on_the_same_day_creates_nothing_and_mails_nothing(env: Env, cases: Cases) -> None:
    with transaction(env.engine, cases.story.tenant) as db:
        enqueue(
            db, service.SWEEP_JOB, tenant_id=cases.story.tenant, payload={}, dedupe_key="again-2026-09-15"
        )
    drain(env.app)
    assert len(mine(cases.api)) == 3 and len(mine(cases.approver)) == 3
    assert len(digests(email(cases.api))) == 1


def test_an_owner_alone_gets_the_7_day_reminder(env: Env, cases: Cases) -> None:
    me = cases.approver.get("/auth/me").json()["user"]["id"]
    c = case_by(cases.api, cases.price)
    assert (
        act(
            cases.api, "patch", f"/cases/{cases.price}", cases.price, {"owner_user_id": me}, c["etag"]
        ).status_code
        == 200
    )
    at(env, 9, 22)
    # admin: 3 + the quantity and unmatched cases = 5; approver: 3 + all three = 6 (the price case is theirs)
    assert len(mine(cases.api)) == 5 and len(mine(cases.approver)) == 6
    assert sum(n["entity_id"] == str(cases.price) for n in mine(cases.api)) == 1  # only the 14-day one
    assert len(digests(email(cases.api))) == 2
    assert len(digests(email(cases.approver))) == 2


def test_3_day_reminder_and_the_deadline_day_does_not_repeat_it(env: Env, cases: Cases) -> None:
    at(env, 9, 26)
    assert len(mine(cases.api)) == 7 and len(mine(cases.approver)) == 9
    at(env, 9, 29)  # deadline day: the 3-day reminder is already there (same dedupe key)
    assert len(mine(cases.api)) == 7 and len(mine(cases.approver)) == 9
    at(env, 9, 30)  # past the deadline: nothing
    assert len(mine(cases.api)) == 7 and len(mine(cases.approver)) == 9
    with transaction(env.engine, cases.story.tenant) as db:
        keys = db.scalars(text("SELECT dedupe_key FROM notifications")).all()
    assert (
        len(keys) == 16 and len(set(keys)) == 9
    )  # 7 + 9 rows; 3 cases x 3 offsets (one key per case and offset)
    assert all(not k.startswith(str(cases.unpaid)) for k in keys)


def test_notifications_are_read_by_their_owner_only(cases: Cases) -> None:
    first = mine(cases.api)[0]
    other = mine(cases.approver)[0]
    assert cases.approver.post(f"/notifications/{first['id']}/read").status_code == 404
    r = cases.api.post(f"/notifications/{first['id']}/read")
    assert r.status_code == 200 and r.json()["read_at"] is not None
    assert cases.api.post(f"/notifications/{other['id']}/read").status_code == 404
    assert len(cases.api.get("/notifications", params={"unread": "true"}).json()["data"]) == 6
    assert UUID(first["id"])
