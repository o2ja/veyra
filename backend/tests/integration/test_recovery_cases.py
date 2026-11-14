"""S9 cases against real Postgres, real imports, runs and claim batches (no stand-ins).

Expected values come from ``testdata/generated/ground_truth`` (``expected_reconciliation.json``: GL-V1 short-pay of
150.00, the 2,080.00 quantity rejection, the 75.00 unmatched payment, the 660.00 unpaid line, the dispute deadline
2026-09-29 = remittance date 2026-07-31 + 60 days) or from the hand derivations in the docstrings. Tests of one
scenario share a module-scoped workspace and run in file order."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from rebate.core.db import transaction
from tests.conftest import DbUrls
from tests.integration.agreements_support import member
from tests.integration.conftest import CSV, clean_file
from tests.integration.recovery_support import (
    LINES,
    NOW,
    Story,
    act,
    build_story,
    case_by,
    confirm_payment,
    finding,
    open_cases,
    remittance,
    seed_remittance,
)
from tests.support import Api, FakeClock, drain, fake_clock, make_app

D = Decimal
DEADLINE = date(2026, 9, 29)  # expected_reconciliation.json: remittance date 2026-07-31 + 60 days
REF = {x["scenario"]: x["invoice_no"] for x in LINES}  # scenario -> invoice number (claim_lines key)


@dataclass
class Env:
    app: FastAPI
    stack: ExitStack
    clock: FakeClock
    engine: Engine


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls, app_engine: Engine) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc, app_engine)


@dataclass
class Cases:
    story: Story
    approver: Api
    analyst: Api
    price: UUID  # INV-10001 (SC-01), short_paid 150.00
    quantity: UUID  # INV-10011 (SC-07b), rejected 2080.00
    unmatched: UUID  # remittance line 3, 75.00
    unpaid: UUID  # INV-10060 (SC-22), 660.00
    remittance: UUID
    rlines: list[UUID]

    @property
    def api(self) -> Api:
        return self.story.ws.api


@pytest.fixture(scope="module")
def cases(env: Env, admin_engine: Engine) -> Cases:
    """The ground-truth credit memo CM-7781 finalised: four cases (expected_reconciliation.json)."""
    story = build_story(env.app, env.stack)
    cl = story.claim_lines
    doc, rl = seed_remittance(
        admin_engine,
        story,
        "CM-7781",
        date(2026, 7, 31),
        [D("2050.00"), D("0.00"), D("75.00")],
        ["E12", "E07", None],
    )
    ids = open_cases(
        env.engine,
        story,
        remittance(
            doc,
            story.manufacturer,
            date(2026, 7, 31),
            (
                finding("short_paid", "150.00", "rejected_price_mismatch", claim_line_id=cl[REF["SC-01"]],
                        remittance_line_id=rl[0], claimed=D("2200.00"), paid=D("2050.00"), external_reason_code="E12"),
                finding("rejected", "2080.00", "rejected_quantity_exceeds_authorization",
                        claim_line_id=cl[REF["SC-07b"]], remittance_line_id=rl[1], claimed=D("2080.00"),
                        paid=D("0.00"), external_reason_code="E07"),
                finding("unmatched_payment", "75.00", "unmatched_payment", claim_line_id=None,
                        remittance_line_id=rl[2], claimed=None, paid=D("75.00")),
                finding("unpaid", "660.00", "unpaid_no_remittance", claim_line_id=cl[REF["SC-22"]],
                        remittance_line_id=None, claimed=D("660.00"), paid=D("0.00")),
            ),
        ),
    )  # fmt: skip
    assert len(ids) == 4
    return Cases(
        story,
        member(env.stack, env.app, story.ws.api, "approver"),
        member(env.stack, env.app, story.ws.api, "analyst"),
        ids[0],
        ids[1],
        ids[2],
        ids[3],
        doc,
        rl,
    )


def four_eyes(api: Api, on: bool) -> None:
    etag = api.get("/settings").headers["ETag"]
    r = api.patch("/settings", json={"four_eyes_required": on}, headers={"If-Match": etag})
    assert r.status_code == 200, r.text


def audit_actions(api: Api, entity_id: UUID | str) -> list[str]:
    r = api.get("/audit-log", params={"entity_id": str(entity_id), "limit": 200})
    assert r.status_code == 200, r.text
    return [e["action"] for e in r.json()["data"]]


def events(api: Api, case_id: UUID) -> list[dict[str, Any]]:
    r = api.get(f"/cases/{case_id}/events")
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


def listing(api: Api, **params: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        r = api.get("/cases", params={"limit": 2, **params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        out += r.json()["data"]
        if not r.json()["page"]["has_more"]:
            return out
        cursor = r.json()["page"]["next_cursor"]


def db_error(engine: Engine, tenant: UUID, sql: str, **params: Any) -> str:
    """The first line of the database's refusal of ``sql`` as the app role in ``tenant`` ('' if it ran)."""
    try:
        with transaction(engine, tenant) as db:
            db.execute(text(sql), params)
    except DBAPIError as exc:
        return str(exc.orig).splitlines()[0]
    return ""


# ---- creation ---------------------------------------------------------------------------------------------------


def test_cases_open_from_the_ground_truth_remittance(cases: Cases) -> None:
    """GL-V1 and the rest of ``expected_reconciliation.json``: 2,200.00 claimed, 2,050.00 paid -> short_paid 150.00,
    reason rejected_price_mismatch (E12); 2,080.00 rejected (E07); 75.00 unmatched; the SC-22 line unpaid 660.00.
    The dispute deadline is 2026-07-31 + 60 = 2026-09-29 (Jul 31 + 31 = Aug 31; + 29 = Sep 29); the unpaid line has no
    remittance to count from, so its deadline counts from the claim submission (D-N2): 2026-07-05 + 60 = 2026-09-03
    (Jul 5 + 26 = Jul 31; + 31 = Aug 31, +57; + 3 = Sep 3, +60)."""
    short = case_by(cases.api, cases.price)
    assert (short["case_type"], short["reason_code"], short["status"]) == (
        "short_paid",
        "rejected_price_mismatch",
        "open",
    )
    assert (D(short["claimed_amount"]), D(short["paid_amount"]), D(short["variance_amount"])) == (
        D("2200.00"), D("2050.00"), D("150.00"))  # fmt: skip
    assert short["external_reason_code"] == "E12" and short["revision"] == 1 and short["etag"] == '"1"'
    assert short["deadline_date"] == DEADLINE.isoformat() and short["deadline_basis"] == "remittance_date"
    assert short["deadline_unknown"] is False and D(short["recovered_amount"]) == 0

    rej = case_by(cases.api, cases.quantity)
    assert (rej["case_type"], D(rej["variance_amount"]), D(rej["paid_amount"])) == (
        "rejected",
        D("2080.00"),
        D("0"),
    )
    assert rej["deadline_date"] == DEADLINE.isoformat()

    unm = case_by(cases.api, cases.unmatched)
    assert (unm["case_type"], unm["claim_line_id"], D(unm["variance_amount"])) == (
        "unmatched_payment",
        None,
        D("75.00"),
    )
    assert unm["claimed_amount"] is None and unm["deadline_date"] == DEADLINE.isoformat()

    unpaid = case_by(cases.api, cases.unpaid)
    assert (unpaid["case_type"], unpaid["reason_code"], D(unpaid["variance_amount"])) == (
        "unpaid", "unpaid_no_remittance", D("660.00"))  # fmt: skip
    assert (
        unpaid["deadline_date"] == "2026-09-03"
        and unpaid["deadline_unknown"] is False
        and unpaid["deadline_basis"] == "submission_date"
    )


def test_dispute_assessment_comes_from_the_frozen_calculation(cases: Cases) -> None:
    """expected_reconciliation.json: E12 -> 'unit_price 88.00 equals agreement sell_price 88.00' = supported;
    E07 -> 'eligible qty 40 within cap 100 (consumption 100)' = supported. A reason with no rule is unknown."""
    a = case_by(cases.api, cases.price)["dispute_assessment"]
    assert a["assessment"] == "supported" and a["reason_code"] == "rejected_price_mismatch"
    (check,) = a["checks"]
    assert check["name"] == "unit_price_matches_sell_price" and check["passed"] is True
    assert D(check["inputs"]["unit_price"]) == D(check["inputs"]["sell_price"]) == D("88.00")

    a = case_by(cases.api, cases.quantity)["dispute_assessment"]
    (check,) = a["checks"]
    assert (
        a["assessment"] == "supported"
        and check["name"] == "consumption_within_max_quantity"
        and check["passed"]
    )
    assert D(check["inputs"]["consumption"]) == D(check["inputs"]["max_quantity"]) == D("100")
    assert D(check["inputs"]["eligible_quantity"]) == D("40")

    unpaid = case_by(cases.api, cases.unpaid)["dispute_assessment"]
    assert unpaid["assessment"] == "unknown" and unpaid["checks"] == []
    assert case_by(cases.api, cases.unmatched)["dispute_assessment"] is None


def test_every_case_has_a_created_event_and_an_audit_entry(cases: Cases) -> None:
    for cid in (cases.price, cases.quantity, cases.unmatched, cases.unpaid):
        (created,) = events(cases.api, cid)
        assert created["event_type"] == "created" and created["actor_user_id"] == str(cases.story.user)
        assert audit_actions(cases.api, cid) == ["variance_case.created"]


def test_a_second_finalisation_does_not_duplicate_a_live_case(
    env: Env, cases: Cases, admin_engine: Engine
) -> None:
    """One live case per claim line and type: a repeated ``unpaid`` finding (it recurs at every finalisation until
    the line is paid) opens nothing."""
    s = cases.story
    doc, _ = seed_remittance(admin_engine, s, "CM-7782", date(2026, 8, 3), [D("5.00")], [None])
    again = remittance(
        doc, s.manufacturer, date(2026, 8, 3),
        (finding("unpaid", "660.00", "unpaid_no_remittance", claim_line_id=s.claim_lines[REF["SC-22"]],
                 remittance_line_id=None, claimed=D("660.00"), paid=D("0.00")),),
    )  # fmt: skip
    assert open_cases(env.engine, s, again) == []
    assert len(listing(cases.api, reason_code="unpaid_no_remittance")) == 1


# ---- queue ------------------------------------------------------------------------------------------------------


def test_queue_puts_unknown_deadlines_first_pages_and_filters(cases: Cases) -> None:
    """Order: ``deadline_unknown`` first, then deadline, then id; no deadline is unknown now, so the unpaid case
    (2026-09-03) precedes the three due 2026-09-29. Paging with limit 2 walks the same order."""
    expected = [str(cases.unpaid), *sorted(str(c) for c in (cases.price, cases.quantity, cases.unmatched))]
    assert [c["id"] for c in listing(cases.api)] == expected
    assert [c["id"] for c in listing(cases.api, deadline_before="2026-09-30")] == expected
    assert [c["id"] for c in listing(cases.api, deadline_before="2026-09-29")] == [
        str(cases.unpaid)
    ]  # strictly before
    assert [c["id"] for c in listing(cases.api, status=["open", "to_reclaim"])] == expected
    assert listing(cases.api, status="submitted") == []
    assert [c["id"] for c in listing(cases.api, reason_code="rejected_price_mismatch")] == [str(cases.price)]
    assert [c["id"] for c in listing(cases.api, manufacturer_id=str(cases.story.manufacturer))] == expected
    assert listing(cases.api, manufacturer_id=str(uuid.uuid4())) == []
    assert cases.api.get("/cases", params={"sort": "x"}).status_code == 400
    assert cases.api.get("/cases", params={"status": "nonsense"}).status_code == 422
    assert cases.api.get("/cases", params={"cursor": "bm9wZQ"}).status_code == 422


def test_summary_totals_by_status_reason_and_at_risk(cases: Cases) -> None:
    """Hand: 150.00 + 2,080.00 + 75.00 + 660.00 = 2,965.00 open. At risk (live, deadline unknown or within 14 days
    of 2026-07-10, i.e. by 2026-07-24): none; the unpaid case falls due 2026-09-03 (07-05 + 60), the rest 2026-09-29."""
    body = cases.api.get("/cases/summary").json()
    assert [(r["key"], r["count"], D(r["variance_amount"])) for r in body["by_status"]] == [
        ("open", 4, D("2965.00"))
    ]
    assert {r["key"]: D(r["variance_amount"]) for r in body["by_reason"]} == {
        "rejected_price_mismatch": D("150.00"),
        "rejected_quantity_exceeds_authorization": D("2080.00"),
        "unmatched_payment": D("75.00"),
        "unpaid_no_remittance": D("660.00"),
    }
    assert (body["at_risk"]["count"], D(body["at_risk"]["amount"]), body["at_risk"]["deadline_within_days"]) == (
        0, D("0.00"), 14)  # fmt: skip


# ---- owner, notes, attachments ------------------------------------------------------------------------------------


def test_assign_owner_needs_the_current_revision_and_a_member(cases: Cases) -> None:
    me = cases.approver.get("/auth/me").json()["user"]["id"]
    case = case_by(cases.api, cases.quantity)
    assert (
        cases.api.patch(f"/cases/{cases.quantity}", json={"owner_user_id": me}).status_code == 412
    )  # no If-Match
    stale = cases.api.patch(
        f"/cases/{cases.quantity}", json={"owner_user_id": me}, headers={"If-Match": '"7"'}
    )
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "precondition_failed"
    nobody = act(
        cases.api, "patch", f"/cases/{cases.quantity}", cases.quantity, {"owner_user_id": str(uuid.uuid4())}
    )
    assert nobody.status_code == 422 and nobody.json()["error"]["details"][0]["code"] == "not_a_member"
    r = act(
        cases.api, "patch", f"/cases/{cases.quantity}", cases.quantity, {"owner_user_id": me}, case["etag"]
    )
    assert r.status_code == 200 and r.json()["owner_user_id"] == me and r.headers["ETag"] == '"2"'
    assert [e["event_type"] for e in events(cases.api, cases.quantity)] == ["created", "assigned"]
    assert listing(cases.api, owner_user_id=me)[0]["id"] == str(cases.quantity)
    again = cases.api.patch(
        f"/cases/{cases.quantity}", json={"owner_user_id": me}, headers={"If-Match": case["etag"]}
    )
    assert again.status_code == 412  # the first change bumped the revision


def test_notes_and_attachments(env: Env, cases: Cases) -> None:
    api = cases.api
    r = api.post(f"/cases/{cases.unpaid}/notes", json={"note": "Called the Voltara credit desk."})
    assert r.status_code == 201 and r.json()["event_type"] == "note" and r.json()["note"].startswith("Called")
    assert api.post(f"/cases/{cases.unpaid}/notes", json={"note": ""}).status_code == 422
    assert api.post(f"/cases/{cases.unpaid}/notes", json={"note": "x", "extra": 1}).status_code == 422
    assert case_by(api, cases.unpaid)["revision"] == 1  # notes do not touch the case row

    csrf = api.csrf()
    up = api.client.post(
        "/api/v1/files",
        files={"file": ("proof.csv", b"claim,paid\nCL-2026-000007,0\n", CSV)},
        data={"kind": "attachment"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
    )
    assert up.status_code == 201, up.text
    fid = up.json()["id"]
    pending = api.post(f"/cases/{cases.unpaid}/attachments", json={"file_id": fid})
    assert pending.status_code == 409  # not scanned yet: quarantined files are not attachable
    drain(env.app)
    ok = api.post(f"/cases/{cases.unpaid}/attachments", json={"file_id": fid})
    assert ok.status_code == 201 and ok.json()["file_id"] == fid
    assert api.post(f"/cases/{cases.unpaid}/attachments", json={"file_id": fid}).status_code == 409
    assert [a["file_id"] for a in api.get(f"/cases/{cases.unpaid}/attachments").json()] == [fid]
    import_file = clean_file(cases.story.ws, "other.csv", b"a,b\n1,2\n")  # kind import
    assert api.post(f"/cases/{cases.unpaid}/attachments", json={"file_id": import_file}).status_code == 409
    assert (
        api.post(f"/cases/{cases.unpaid}/attachments", json={"file_id": str(uuid.uuid4())}).status_code == 404
    )
    kinds = [e["event_type"] for e in events(api, cases.unpaid)]
    assert kinds == ["created", "note", "attachment"]


# ---- state machine over the API -------------------------------------------------------------------------------------


def test_transitions_follow_the_state_machine(cases: Cases) -> None:
    api, cid = cases.api, cases.unmatched
    assert act(api, "post", f"/cases/{cid}/transition", cid, {"to_status": "submitted"}).status_code == 409
    assert act(api, "post", f"/cases/{cid}/transition", cid, {"to_status": "recovered"}).status_code == 409
    illegal = act(api, "post", f"/cases/{cid}/transition", cid, {"to_status": "open"})
    assert illegal.status_code == 409 and illegal.json()["error"]["code"] == "invalid_state"
    dedicated = act(api, "post", f"/cases/{cid}/transition", cid, {"to_status": "to_reclaim"})
    assert (
        dedicated.status_code == 422
        and dedicated.json()["error"]["details"][0]["code"] == "use_dedicated_endpoint"
    )
    assert case_by(api, cid)["status"] == "open"

    stale = api.post(
        f"/cases/{cid}/transition", json={"to_status": "closed_no_action"}, headers={"If-Match": '"9"'}
    )
    assert stale.status_code == 412
    r = act(
        api,
        "post",
        f"/cases/{cid}/transition",
        cid,
        {"to_status": "closed_no_action", "note": "Customer credit"},
    )
    assert r.status_code == 200 and r.json()["status"] == "closed_no_action" and r.json()["closed_at"]
    last = events(api, cid)[-1]
    assert (last["event_type"], last["from_status"], last["to_status"], last["note"]) == (
        "status_changed", "open", "closed_no_action", "Customer credit")  # fmt: skip
    assert "variance_case.status_changed" in audit_actions(api, cid)
    for target in ("open", "to_reclaim", "submitted", "closed_no_action"):  # terminal: nothing leaves it
        assert act(api, "post", f"/cases/{cid}/transition", cid, {"to_status": target}).status_code in (
            409,
            422,
        )
    assert act(api, "patch", f"/cases/{cid}", cid, {"owner_user_id": None}).status_code == 409


def test_reclaim_proposes_an_adjustment_and_payments_recover_the_case(
    env: Env, cases: Cases, admin_engine: Engine
) -> None:
    """Case 2,080.00: reclaim -> to_reclaim + a recovery_reclaim adjustment of 2,080.00 (positive). A payment of
    1,000.00 matched to it moves the case to submitted and leaves it there (partial); a second of 1,080.00 completes
    it: 1,000.00 + 1,080.00 = 2,080.00 -> recovered. Applying the same remittance again changes nothing."""
    api, cid, s = cases.api, cases.quantity, cases.story
    assert (
        act(
            api, "post", f"/cases/{cases.unpaid}/transition", cases.unpaid, {"to_status": "to_reclaim"}
        ).status_code
        == 422
    )
    r = act(api, "post", f"/cases/{cid}/reclaim", cid)
    assert r.status_code == 200, r.text
    adjustment = r.json()["adjustment_id"]
    assert (
        r.json()["case"]["status"] == "to_reclaim"
        and r.headers["ETag"] == f'"{r.json()["case"]["revision"]}"'
    )
    (adj,) = api.get(
        "/claim-adjustments", params={"claim_line_id": str(s.claim_lines[REF["SC-07b"]])}
    ).json()["data"]
    assert (
        adj["id"] == adjustment
        and adj["adjustment_type"] == "recovery_reclaim"
        and adj["status"] == "proposed"
    )
    assert D(adj["amount"]) == D("2080.00")
    assert "claim_adjustment.proposed" in audit_actions(api, adjustment)
    assert [e["event_type"] for e in events(api, cid)][-2:] == ["reclaim_proposed", "status_changed"]
    assert act(api, "post", f"/cases/{cid}/reclaim", cid).status_code == 409  # already to_reclaim
    assert act(api, "post", f"/cases/{cases.unmatched}/reclaim", cases.unmatched).status_code == 409  # closed

    doc1, rl1 = seed_remittance(admin_engine, s, "CM-7790", date(2026, 8, 20), [D("1000.00")], [None])
    confirm_payment(admin_engine, s, rl1[0], adjustment, D("1000.00"))
    first = remittance(doc1, s.manufacturer, date(2026, 8, 20), ())
    open_cases(env.engine, s, first)
    part = case_by(api, cid)
    assert (part["status"], D(part["recovered_amount"]), part["closed_at"]) == (
        "submitted",
        D("1000.00"),
        None,
    )
    assert [e["to_status"] for e in events(api, cid) if e["event_type"] == "status_changed"] == [
        "to_reclaim",
        "submitted",
    ]
    paid = [e for e in events(api, cid) if e["event_type"] == "amount_recovered"]
    assert len(paid) == 1 and D(paid[0]["payload"]["amount"]) == D("1000.00")
    assert D(paid[0]["payload"]["recovered_total"]) == D("1000.00") and paid[0]["payload"][
        "remittance_id"
    ] == str(doc1)

    open_cases(env.engine, s, first)  # the same remittance applied twice
    assert case_by(api, cid)["revision"] == part["revision"]

    doc2, rl2 = seed_remittance(admin_engine, s, "CM-7791", date(2026, 9, 10), [D("1080.00")], [None])
    confirm_payment(admin_engine, s, rl2[0], adjustment, D("1080.00"))
    open_cases(env.engine, s, remittance(doc2, s.manufacturer, date(2026, 9, 10), ()))
    done = case_by(api, cid)
    assert (done["status"], D(done["recovered_amount"])) == ("recovered", D("2080.00")) and done["closed_at"]
    assert [e["to_status"] for e in events(api, cid) if e["event_type"] == "status_changed"][
        -1
    ] == "recovered"
    assert "variance_case.recovery_recorded" in audit_actions(api, cid)
    assert act(api, "post", f"/cases/{cid}/transition", cid, {"to_status": "to_reclaim"}).status_code in (
        409,
        422,
    )


# ---- write-off, four-eyes -----------------------------------------------------------------------------------------------


def test_write_off_needs_an_approver_and_a_second_person(cases: Cases) -> None:
    """Workspace four-eyes ON: the first approver's call proposes, the same person cannot finish it (403, audited
    even though the request failed), a different approver writes off: amount = 150.00 - 0.00 recovered."""
    api, cid = cases.api, cases.price
    four_eyes(api, True)
    body = {"reason": "Customer relationship; not worth chasing"}
    denied = act(cases.analyst, "post", f"/cases/{cid}/write-off", cid, body)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"

    proposed = act(api, "post", f"/cases/{cid}/write-off", cid, body)
    assert proposed.status_code == 200 and proposed.json()["status"] == "open"
    assert (
        proposed.json()["write_off_proposed_by"] == str(cases.story.user)
        and proposed.json()["written_off_by"] is None
    )
    assert (
        events(api, cid)[-1]["event_type"] == "write_off_proposed"
        and events(api, cid)[-1]["note"] == body["reason"]
    )

    same = act(api, "post", f"/cases/{cid}/write-off", cid, body)
    assert same.status_code == 403 and same.json()["error"]["code"] == "four_eyes_violation"
    assert "variance_case.four_eyes_violation" in audit_actions(api, cid)  # committed with the refusal
    assert case_by(api, cid)["status"] == "open"

    other = act(cases.approver, "post", f"/cases/{cid}/write-off", cid, {"reason": "Agreed"})
    assert other.status_code == 200, other.text
    out = other.json()
    approver = cases.approver.get("/auth/me").json()["user"]["id"]
    assert (out["status"], D(out["written_off_amount"]), D(out["recovered_amount"])) == (
        "written_off",
        D("150.00"),
        D("0"),
    )
    assert out["written_off_by"] == approver and out["write_off_proposed_by"] == str(cases.story.user)
    assert out["closed_at"] and out["written_off_at"]
    # the approver signs off the proposal: their own body reason ("Agreed") is ignored
    assert out["write_off_reason"] == body["reason"] and events(api, cid)[-1]["note"] == body["reason"]
    acts = audit_actions(api, cid)
    assert "variance_case.write_off_proposed" in acts and "variance_case.written_off" in acts
    last = events(api, cid)[-1]
    assert (last["from_status"], last["to_status"], last["actor_user_id"]) == (
        "open",
        "written_off",
        approver,
    )
    assert act(cases.approver, "post", f"/cases/{cid}/write-off", cid, body).status_code == 409
    assert act(api, "post", f"/cases/{cid}/reclaim", cid).status_code == 409


def test_with_four_eyes_off_one_approver_writes_off_at_once(cases: Cases) -> None:
    four_eyes(cases.api, False)
    cid = cases.unpaid
    body, key = {"reason": "Line disputed by customer"}, str(uuid.uuid4())
    path, etag = f"/cases/{cid}/write-off", case_by(cases.api, cid)["etag"]
    assert (
        cases.api.post(path, json=body, headers={"If-Match": etag}).status_code == 400
    )  # Idempotency-Key required
    r = cases.api.post(path, json=body, headers={"If-Match": etag, "Idempotency-Key": key})
    assert (
        r.status_code == 200
        and r.json()["status"] == "written_off"
        and D(r.json()["written_off_amount"]) == D("660.00")
    )
    assert r.json()["write_off_proposed_by"] is None
    # same key and body: the first answer again (without the key it would be 409, the case is closed)
    again = cases.api.post(path, json=body, headers={"If-Match": etag, "Idempotency-Key": key})
    assert again.status_code == 200 and again.json() == r.json()
    assert again.headers["ETag"] == r.headers["ETag"] == f'"{r.json()["revision"]}"'
    other = cases.api.post(path, json={"reason": "x"}, headers={"If-Match": etag, "Idempotency-Key": key})
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    four_eyes(cases.api, True)


# ---- the database says no too -----------------------------------------------------------------------------------------


def test_database_refuses_what_the_service_never_does(env: Env, cases: Cases, admin_engine: Engine) -> None:
    """Invariants live in constraints and triggers (CLAUDE.md §11): driven here as the app role, bypassing the service."""
    s = cases.story
    doc, rl = seed_remittance(admin_engine, s, "CM-7800", date(2026, 9, 1), [D("900.00")], [None])
    (cid,) = open_cases(
        env.engine, s,
        remittance(doc, s.manufacturer, date(2026, 9, 1),
            (finding("short_paid", "100.00", "unknown", claim_line_id=s.claim_lines[REF["SC-20"]],
                     remittance_line_id=rl[0], claimed=D("1000.00"), paid=D("900.00")),)),
    )  # fmt: skip
    t = s.tenant

    def run(sql: str, **p: Any) -> str:
        return db_error(env.engine, t, sql, **p)

    bump = "revision = revision + 1"
    assert "not a case transition" in run(
        f"UPDATE variance_cases SET status = 'recovered', {bump} WHERE id = :c", c=cid
    )
    assert "bumps the revision" in run("UPDATE variance_cases SET owner_user_id = NULL WHERE id = :c", c=cid)
    assert "fixed" in run(f"UPDATE variance_cases SET variance_amount = 1, {bump} WHERE id = :c", c=cid)
    assert "fixed" in run(f"UPDATE variance_cases SET case_type = 'rejected', {bump} WHERE id = :c", c=cid)
    assert "ck_variance_cases_four_eyes" in run(
        "UPDATE variance_cases SET status = 'written_off', written_off_by = :u, written_off_at = now(), "
        f"written_off_amount = 100, write_off_proposed_by = :u, write_off_proposed_at = now(), closed_at = now(), {bump} WHERE id = :c",
        c=cid, u=s.user)  # fmt: skip
    assert "permission denied" in run("DELETE FROM variance_cases WHERE id = :c", c=cid)
    assert "permission denied" in run("UPDATE variance_case_events SET note = 'x' WHERE case_id = :c", c=cid)
    assert "permission denied" in run("DELETE FROM variance_case_events WHERE case_id = :c", c=cid)
    assert "permission denied" in run("DELETE FROM case_attachments WHERE case_id = :c", c=cid)
    assert "uq_variance_cases_live_claim_line" in run(
        "INSERT INTO variance_cases (tenant_id, id, manufacturer_id, claim_line_id, case_type, reason_code, "
        "variance_amount) SELECT tenant_id, gen_random_uuid(), manufacturer_id, claim_line_id, case_type, reason_code, "
        "variance_amount FROM variance_cases WHERE id = :c", c=cid)  # fmt: skip
    assert "new case is open" in run(
        "INSERT INTO variance_cases (tenant_id, id, manufacturer_id, claim_line_id, case_type, reason_code, "
        "variance_amount, status) SELECT tenant_id, gen_random_uuid(), manufacturer_id, claim_line_id, 'rejected', "
        "reason_code, variance_amount, 'recovered' FROM variance_cases WHERE id = :c", c=cid)  # fmt: skip
    # a closed case never changes, whatever the statement
    closed = cases.price
    assert "never changes" in run(
        f"UPDATE variance_cases SET owner_user_id = NULL, {bump} WHERE id = :c", c=closed
    )
    # a file that is not a clean attachment cannot be attached (the trigger, not only the service)
    other = clean_file(s.ws, "dbtest.csv", b"a,b\n3,4\n")
    assert "clean-scanned attachment" in run(
        "INSERT INTO case_attachments (tenant_id, id, case_id, file_id, added_by) VALUES (:t, gen_random_uuid(), :c, :f, :u)",
        t=t, c=cid, f=other, u=s.user)  # fmt: skip
