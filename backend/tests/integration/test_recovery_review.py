"""Review fixes of S9 (docs/reports/notes/s9-review-1.md): a write-off retires the case's reclaim, a four-eyes proposal
does not outlive a status change, reminders go to active members only, titles state the days really left, the
submission_date basis, blank text. Fresh workspace (the CM-7781 cases); tests run in file order."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext
from rebate.modules.recovery import jobs, service
from tests.integration import test_recovery_cases as base
from tests.integration.calc_support import settings
from tests.integration.recovery_support import act, case_by, finding, open_cases, remittance, seed_remittance
from tests.integration.test_adjustment_flows import new_batch
from tests.integration.test_recovery_cases import REF, Cases, Env, audit_actions, events, four_eyes
from tests.integration.test_recovery_jobs import at, digests, email

env, cases = base.env, base.cases
D = Decimal


def adjustment(api: Any, adjustment_id: str) -> dict[str, Any]:
    r = api.get("/claim-adjustments", params={"limit": 200})
    assert r.status_code == 200, r.text
    (a,) = [x for x in r.json()["data"] if x["id"] == adjustment_id]
    return dict(a)


def test_write_off_rejects_the_live_reclaim_in_the_same_transaction(cases: Cases) -> None:
    """2,080.00 rejected case: reclaim proposes an adjustment of 2,080.00; the write-off (four-eyes off) writes off
    2,080.00 - 0.00 and the adjustment is rejected, so it can never be batched and paid on top."""
    api, cid = cases.api, cases.quantity
    four_eyes(api, False)
    adj = act(api, "post", f"/cases/{cid}/reclaim", cid).json()["adjustment_id"]
    assert adjustment(api, adj)["status"] == "proposed"
    r = act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "Not worth chasing"})
    assert r.status_code == 200 and r.json()["status"] == "written_off"
    assert D(r.json()["written_off_amount"]) == D("2080.00")
    assert adjustment(api, adj)["status"] == "rejected"
    assert "claim_adjustment.rejected" in audit_actions(api, adj)


def test_write_off_is_refused_while_the_reclaim_sits_in_a_batch(cases: Cases) -> None:
    """150.00 short-paid: reclaim -> approved -> in a draft batch (150.00). Write-off: 409 reclaim_included and
    nothing changed; after the batch is voided the reclaim is approved again and the write-off rejects it."""
    api, cid = cases.api, cases.price
    adj = act(api, "post", f"/cases/{cid}/reclaim", cid).json()["adjustment_id"]
    assert cases.approver.post(f"/claim-adjustments/{adj}/approve").status_code == 200
    spare = api.get("/missed-claims", params={"manufacturer_id": str(cases.story.manufacturer)}).json()[
        "data"
    ]
    assert spare, "the batch needs one eligible line to carry the adjustment"
    batch = new_batch(
        api, str(cases.story.manufacturer), result_ids=[spare[0]["id"]], include_adjustment_ids=[adj]
    )
    assert batch.status_code == 201, batch.text
    assert adjustment(api, adj)["status"] == "included"
    before = case_by(api, cid)
    r = act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "Give up"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "reclaim_included"
    after = case_by(api, cid)
    assert (after["status"], after["revision"]) == ("to_reclaim", before["revision"])
    assert adjustment(api, adj)["status"] == "included"
    void = cases.approver.post(f"/claim-batches/{batch.json()['id']}/void", json={"reason": "test"})
    assert void.status_code == 200
    done = act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "Give up"})
    assert done.status_code == 200 and D(done.json()["written_off_amount"]) == D("150.00")
    assert adjustment(api, adj)["status"] == "rejected"


def test_a_four_eyes_proposal_does_not_survive_a_status_change_and_the_audit_keeps_the_reasons(
    cases: Cases,
) -> None:
    """660.00 unpaid, four-eyes on: the admin proposes a write-off, then reclaims (open -> to_reclaim): the proposal
    is void. The approver's write-off is a new proposal, the admin completes it; 660.00 written off, the reclaim
    rejected. The audit entries carry the reason and the write-off reason."""
    api, cid = cases.api, cases.unpaid
    four_eyes(api, True)
    first = act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "First thoughts"})
    assert first.json()["write_off_proposed_by"] == str(cases.story.user)
    moved = act(api, "post", f"/cases/{cid}/reclaim", cid)
    assert moved.status_code == 200
    adj, case = moved.json()["adjustment_id"], moved.json()["case"]
    assert case["status"] == "to_reclaim" and case["write_off_proposed_by"] is None
    assert case["write_off_reason"] is None
    prop = act(cases.approver, "post", f"/cases/{cid}/write-off", cid, {"reason": "Customer insolvent"})
    assert prop.status_code == 200 and prop.json()["status"] == "to_reclaim"
    assert prop.json()["write_off_proposed_by"] == cases.approver.get("/auth/me").json()["user"]["id"]
    done = act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "Agreed"})
    assert done.status_code == 200, done.text
    assert (done.json()["status"], D(done.json()["written_off_amount"])) == ("written_off", D("660.00"))
    assert adjustment(api, adj)["status"] == "rejected"
    entries = api.get("/audit-log", params={"entity_id": str(cid), "limit": 200}).json()["data"]
    proposed = next(
        e
        for e in entries
        if e["action"] == "variance_case.write_off_proposed"
        and e["after"]["write_off_reason"] == "Customer insolvent"
    )
    assert proposed["after"]["reason"] == "unpaid_no_remittance"
    final = next(e for e in entries if e["action"] == "variance_case.written_off")
    assert final["after"]["write_off_reason"] == "Customer insolvent"
    assert final["after"]["reason"] == "unpaid_no_remittance"
    assert events(api, cid)[-1]["to_status"] == "written_off"


def test_reminders_skip_a_member_who_left_and_state_the_days_really_left(
    env: Env, cases: Cases, admin_engine: Engine
) -> None:
    """The 75.00 unmatched case is owned by the approver, who then leaves the workspace; the 150.00 case (written
    off above) is closed, so the 75.00 case is the live dated one. A sweep on 2026-09-27 (2 days before 2026-09-29;
    the 14/7/3-day dates were missed, so the 3-day reminder is the one created) goes to the admin only, says
    'in 2 days', and the ex-member gets no mail."""
    api = cases.api
    me = cases.approver.get("/auth/me").json()["user"]["id"]
    approver_mail = email(cases.approver)
    c = case_by(api, cases.unmatched)
    r = act(api, "patch", f"/cases/{cases.unmatched}", cases.unmatched, {"owner_user_id": me}, c["etag"])
    assert r.status_code == 200
    with admin_engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET status = 'disabled' WHERE tenant_id = :t AND user_id = :u"),
            {"t": cases.story.tenant, "u": me},
        )
    at(env, 9, 27)
    with transaction(env.engine, cases.story.tenant) as db:
        rows = db.execute(text("SELECT user_id, title FROM notifications ORDER BY title")).all()
    assert [str(r.user_id) for r in rows] == [str(cases.story.user)]  # nothing for the ex-member
    assert rows[0].title.startswith("Unmatched payment 75.0000") and rows[0].title.endswith(
        "deadline in 2 days"
    )
    assert digests(approver_mail) == []
    (mail,) = digests(email(api))
    assert "75.00" in mail["Text"]


def test_dispute_deadline_counts_from_the_submission_date_when_that_is_the_basis(
    env: Env, cases: Cases, admin_engine: Engine
) -> None:
    """VOLT set to submission_date (60 days): a finding on the SC-22 line gets submitted_at + 60 days."""
    s = cases.story
    settings(s.ws, "VOLT", dispute_window_basis="submission_date")
    with admin_engine.connect() as conn:
        submitted = conn.execute(
            text(
                "SELECT b.submitted_at FROM claim_lines c JOIN claim_batches b ON b.tenant_id = c.tenant_id "
                "AND b.id = c.batch_id WHERE c.id = :c"
            ),
            {"c": s.claim_lines[REF["SC-22"]]},
        ).scalar_one()
    doc, _ = seed_remittance(admin_engine, s, "CM-7795", date(2026, 8, 3), [D("5.00")], [None])
    one = finding(
        "short_paid",
        "10.00",
        "rejected_price_mismatch",
        claim_line_id=s.claim_lines[REF["SC-22"]],
        remittance_line_id=None,
        claimed=D("660.00"),
        paid=D("650.00"),
    )
    (case_id,) = open_cases(env.engine, s, remittance(doc, s.manufacturer, date(2026, 8, 3), (one,)))
    got = case_by(cases.api, case_id)
    assert got["deadline_basis"] == "submission_date"
    assert got["deadline_date"] == (submitted + timedelta(days=60)).isoformat()


def test_summary_sums_what_was_written_off_and_the_database_checks_it(
    cases: Cases, admin_engine: Engine
) -> None:
    """Written off so far: 2,080.00 + 150.00 + 660.00 = 2,890.00 (the variance of those cases equals it, nothing
    was recovered). The database refuses a write-off whose amount is not variance - recovered."""
    rows = {r["key"]: r for r in cases.api.get("/cases/summary").json()["by_status"]}
    assert rows["written_off"]["count"] == 3
    assert D(rows["written_off"]["written_off_amount"]) == D("2890.00")
    assert D(rows["open"]["written_off_amount"]) == 0
    try:
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE variance_cases SET status = 'written_off', written_off_by = :u, written_off_at = now(), "
                    "closed_at = now(), written_off_amount = 1, revision = revision + 1 WHERE id = :c"
                ),
                {"u": cases.story.user, "c": cases.unmatched},
            )
    except Exception as exc:
        assert "ck_variance_cases_written_off_amount" in str(exc)
    else:
        raise AssertionError("a write-off of 1.00 on a 75.00 case was accepted")


def test_a_dead_sweep_still_schedules_the_next_one(env: Env, cases: Cases) -> None:
    t = cases.story.tenant
    ctx = JobContext(env.app.state.settings, new_id(), t, {}, 5, None, env.engine)
    with transaction(env.engine, t) as db:
        jobs._sweep_died(db, ctx)
        tomorrow = service.business_today(db, t) + timedelta(days=1)
        n = db.scalar(
            text("SELECT count(*) FROM jobs WHERE tenant_id = :t AND dedupe_key = :k"),
            {"t": t, "k": f"recovery_sweep:{t}:{tomorrow.isoformat()}"},
        )
    assert n == 1


def test_blank_notes_and_reasons_are_refused(cases: Cases) -> None:
    api, cid = cases.api, cases.unmatched
    assert api.post(f"/cases/{cid}/notes", json={"note": "   "}).status_code == 422
    assert act(api, "post", f"/cases/{cid}/write-off", cid, {"reason": "   "}).status_code == 422
    assert api.post(f"/cases/{cid}/notes", json={"note": " ok "}).json()["note"] == "ok"
