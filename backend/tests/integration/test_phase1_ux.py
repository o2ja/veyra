"""Phase 1 UX backend: safe file delete/archive, import discard, draft delete, case deadlines and links, remittance
line variance and links, unknown reason codes, the home attention endpoint. Real Postgres, real API.

Money expectations are hand-derived: the ground-truth story (``claim_batches_fixture.json``: SC-01 claimed 2,200.00,
SC-07b 2,080.00) and ``expected_calculation.json`` (eleven eligible scenarios, 11,430.50; the seven claimed lines
10,410.00, so 1,020.50 stays unclaimed). Deadlines: VOLT dispute window 60 days from the remittance date; today is
2026-07-10 (``recovery_support.NOW``)."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from rebate.core.db import transaction
from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.integration.agreements_support import member
from tests.integration.conftest import CSV, Workspace, new_workspace, upload
from tests.integration.recovery_support import (
    LINES,
    NOW,
    Story,
    build_story,
    finding,
    open_cases,
    remittance,
    seed_remittance,
)
from tests.support import Api, FakeClock, api_client, drain, fake_clock, make_app
from tests.testdata import truth

D = Decimal
REF = {x["scenario"]: x["invoice_no"] for x in LINES}
CALC = truth("expected_calculation.json")["scenarios"]
FIX7 = {f"{x['invoice_no']}/{x['line_no']}" for x in LINES}


def audit_actions(api: Api, entity_id: UUID | str) -> list[str]:
    r = api.get("/audit-log", params={"entity_id": str(entity_id), "limit": 200})
    assert r.status_code == 200, r.text
    return [e["action"] for e in r.json()["data"]]


# ---- files and imports (a light workspace of their own) ---------------------------------------------------------


@pytest.fixture
def light(app: FastAPI, clk: FakeClock) -> Iterator[Workspace]:
    with api_client(app) as api:
        yield new_workspace(app, api, master=False)


def _clean(ws: Workspace, name: str, body: bytes) -> str:
    r = upload(ws.api, name, body, CSV)
    assert r.status_code == 201, r.text
    drain(ws.app)
    return str(r.json()["id"])


def _object_exists(ws: Workspace, key: str) -> bool:
    try:
        storage.client(ws.app.state.settings).head_object(Bucket=ws.app.state.settings.s3_bucket, Key=key)
    except ClientError:
        return False
    return True


def test_an_unused_file_is_deleted_with_its_object(light: Workspace, admin_engine: Engine) -> None:
    body = b"customer_code,name\nC-DEL-1,Delete Me (SYNTHETIC)\n"
    fid = _clean(light, "unused.csv", body)
    detail = light.api.get(f"/files/{fid}").json()
    assert detail["used_by"] == [] and detail["archived_at"] is None
    with admin_engine.connect() as conn:
        key = conn.execute(
            text("SELECT storage_key FROM file_objects WHERE id = :i"), {"i": fid}
        ).scalar_one()
    assert _object_exists(light, key)
    r = light.api.delete(f"/files/{fid}")
    assert r.status_code == 200 and r.json() == {"result": "deleted"}, r.text
    assert light.api.get(f"/files/{fid}").status_code == 404
    assert "file.deleted" in audit_actions(light.api, fid)
    drain(light.app)  # files_delete_objects, after the commit
    assert not _object_exists(light, key)
    # the same content may be uploaded again (the deleted row no longer claims it)
    assert upload(light.api, "unused.csv", body, CSV).status_code == 201


def test_a_file_still_being_scanned_cannot_be_deleted(light: Workspace) -> None:
    r = upload(light.api, "pending.csv", b"customer_code,name\nC-P,Pending (SYNTHETIC)\n", CSV)
    r = light.api.delete(f"/files/{r.json()['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


def test_a_used_file_is_archived_and_the_import_waiting_for_you_can_be_discarded(light: Workspace) -> None:
    fid = _clean(light, "customers.csv", b"customer_code,name\nC-ARC-1,Archive Me (SYNTHETIC)\n")
    r = light.api.post(
        "/imports",
        json={"file_id": fid, "import_type": "customers", "source_system": "ERP1"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 202, r.text
    drain(light.app)
    imp = light.api.get(f"/imports/{r.json()['import']['id']}").json()
    assert imp["status"] == "awaiting_confirmation", imp
    assert light.api.get(f"/files/{fid}").json()["used_by"] == [
        {"kind": "import", "id": imp["id"], "label": "Customers import (awaiting_confirmation)"}
    ]

    r = light.api.post(f"/imports/{imp['id']}/discard")
    assert r.status_code == 200 and r.json()["status"] == "discarded", r.text
    assert "import.discarded" in audit_actions(light.api, imp["id"])
    r = light.api.post(f"/imports/{imp['id']}/discard")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"
    assert light.api.post(f"/imports/{imp['id']}/confirm", json={"accept_partial": False}).status_code == 409

    r = light.api.delete(f"/files/{fid}")  # the discarded import still points at it
    assert r.status_code == 200 and r.json() == {"result": "archived"}, r.text
    assert light.api.get(f"/files/{fid}").json()["archived_at"] is not None
    assert fid not in {f["id"] for f in light.api.get("/files").json()["data"]}
    assert fid in {f["id"] for f in light.api.get("/files", params={"include_archived": True}).json()["data"]}
    assert "file.archived" in audit_actions(light.api, fid)
    assert light.api.delete(f"/files/{fid}").json() == {"result": "archived"}  # idempotent
    assert audit_actions(light.api, fid).count("file.archived") == 1


def test_an_import_that_is_still_processing_cannot_be_discarded(light: Workspace) -> None:
    r = upload(light.api, "scanning.csv", b"customer_code,name\nC-S,Scanning (SYNTHETIC)\n", CSV)
    r = light.api.post(
        "/imports",
        json={"file_id": r.json()["id"], "import_type": "customers", "source_system": "ERP1"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.json()["import"]["status"] == "scanning"
    r = light.api.post(f"/imports/{r.json()['import']['id']}/discard")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


def test_the_app_role_deletes_only_through_the_guarded_function(light: Workspace, app_engine: Engine) -> None:
    """A file a record points at survives even a direct call of the function (the composite FK refuses)."""
    fid = _clean(light, "guarded.csv", b"customer_code,name\nC-G,Guarded (SYNTHETIC)\n")
    r = light.api.post(
        "/imports",
        json={"file_id": fid, "import_type": "customers", "source_system": "ERP1"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 202
    with pytest.raises(IntegrityError, match="fk_import_batches_file_objects"):
        with transaction(app_engine, UUID(light.tenant_id)) as db:
            db.execute(text("SELECT fn_delete_unreferenced_file(:i)"), {"i": fid})
    assert light.api.get(f"/files/{fid}").status_code == 200


# ---- the story: cases, remittances, home -------------------------------------------------------------------------


@dataclass
class World:
    story: Story
    analyst: Api
    overdue: UUID  # SC-01 short_paid 150.00, remittance 2026-04-01 -> deadline 2026-05-31
    due_soon: UUID  # SC-07b rejected 2,080.00, remittance 2026-05-20 -> deadline 2026-07-19
    unmatched: UUID  # 75.00 unmatched payment (not reclaimable)
    r1: UUID
    r1_lines: list[UUID]
    batch: dict[str, Any]

    @property
    def api(self) -> Api:
        return self.story.ws.api


@pytest.fixture(scope="module")
def world(migrated_db: DbUrls, app_engine: Engine, admin_engine: Engine) -> Iterator[World]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        story = build_story(app, stack)
        cl = story.claim_lines
        r1, l1 = seed_remittance(
            admin_engine, story, "CM-P1-1", date(2026, 4, 1), [D("2050.00"), D("10.00")], ["E12", "ZZ1"],
            claimed=[D("2200.00"), None],
        )  # fmt: skip
        r2, l2 = seed_remittance(
            admin_engine, story, "CM-P1-2", date(2026, 5, 20), [D("0.00"), D("75.00")], ["ZZ1", "ZZ2"]
        )
        (overdue,) = open_cases(app_engine, story, remittance(r1, story.manufacturer, date(2026, 4, 1), (
            finding("short_paid", "150.00", "rejected_price_mismatch", claim_line_id=cl[REF["SC-01"]],
                    remittance_line_id=l1[0], claimed=D("2200.00"), paid=D("2050.00"), external_reason_code="E12"),
        )))  # fmt: skip
        due_soon, unmatched = open_cases(app_engine, story, remittance(r2, story.manufacturer, date(2026, 5, 20), (
            finding("rejected", "2080.00", "unknown", claim_line_id=cl[REF["SC-07b"]], remittance_line_id=l2[0],
                    claimed=D("2080.00"), paid=D("0.00"), external_reason_code="ZZ1"),
            finding("unmatched_payment", "75.00", "unmatched_payment", claim_line_id=None, remittance_line_id=l2[1],
                    claimed=None, paid=D("75.00")),
        )))  # fmt: skip
        with admin_engine.begin() as c:  # what S8 persists when a person confirms the match of line 1
            c.execute(
                text(
                    "INSERT INTO reconciliation_matches (tenant_id, id, match_id, remittance_line_id, claim_line_id, "
                    "allocated_amount, method, evidence, status, confirmed_by, confirmed_at) "
                    "VALUES (:t, :id, :id, :rl, :cl, 2050.00, 'manual', '{}', 'confirmed', :u, now())"
                ),
                {"t": story.tenant, "id": uuid.uuid4(), "rl": l1[0], "cl": cl[REF["SC-01"]], "u": story.user},
            )
        (batch,) = [
            b for b in story.ws.api.get("/claim-batches").json()["data"] if b["status"] == "submitted"
        ]
        yield World(
            story, member(stack, app, story.ws.api, "analyst"), overdue, due_soon, unmatched, r1, l1, batch
        )


def test_cases_carry_overdue_days_and_their_claim_links(world: World) -> None:
    lines = {x["id"]: x for x in world.api.get(f"/claim-batches/{world.batch['id']}/lines").json()["data"]}
    a = world.api.get(f"/cases/{world.overdue}").json()
    assert a["deadline_date"] == "2026-05-31" and a["overdue_days"] == 40  # 05-31 -> 07-10: 30 + 10 days
    line = lines[a["claim_line_id"]]
    assert (a["claim_batch_id"], a["claim_reference"], a["sales_document_line_id"]) == (
        world.batch["id"], line["claim_reference"], line["sales_document_line_id"])  # fmt: skip
    b = world.api.get(f"/cases/{world.due_soon}").json()
    assert b["deadline_date"] == "2026-07-19" and b["overdue_days"] is None
    u = world.api.get(f"/cases/{world.unmatched}").json()
    assert u["overdue_days"] is None and u["claim_batch_id"] is None and u["sales_document_line_id"] is None
    listed = {c["id"]: c for c in world.api.get("/cases", params={"limit": 200}).json()["data"]}
    assert listed[str(world.overdue)]["overdue_days"] == 40
    assert listed[str(world.overdue)]["claim_batch_id"] == world.batch["id"]


def test_case_events_name_the_actor(world: World) -> None:
    me = world.api.get("/auth/me").json()["user"]
    member = next(u for u in world.api.get("/users").json()["data"] if u["user_id"] == me["id"])
    (created,) = world.api.get(f"/cases/{world.overdue}/events").json()["data"]
    assert created["actor_user_id"] == me["id"]
    assert (created["actor_name"], created["actor_email"]) == (member["full_name"], me["email"])


def test_summary_separates_overdue_from_expiring(world: World) -> None:
    """Hand: overdue = SC-01 150.00; expiring (07-10..07-24) = SC-07b 2,080.00; D-N6 at-risk keeps both: 2,230.00.
    The unmatched payment is not reclaimable, so it is in none of them."""
    s = world.api.get("/cases/summary").json()
    assert (s["overdue"]["count"], D(s["overdue"]["amount"])) == (1, D("150.00"))
    assert (s["expiring"]["count"], D(s["expiring"]["amount"]), s["expiring"]["deadline_within_days"]) == (
        1, D("2080.00"), 14)  # fmt: skip
    assert (s["at_risk"]["count"], D(s["at_risk"]["amount"])) == (2, D("2230.00"))


def test_remittance_lines_show_variance_and_the_matched_claim(world: World) -> None:
    lines = {x["id"]: x for x in world.api.get(f"/remittances/{world.r1}/lines").json()["data"]}
    one, two = lines[str(world.r1_lines[0])], lines[str(world.r1_lines[1])]
    assert D(one["variance"]) == D("150.00")  # stated claimed 2,200.00 - paid 2,050.00
    assert two["variance"] is None  # no claimed amount stated
    case = world.api.get(f"/cases/{world.overdue}").json()
    (m,) = one["matched_claim_lines"]
    assert (m["claim_line_id"], m["claim_batch_id"], m["claim_reference"]) == (
        case["claim_line_id"], world.batch["id"], case["claim_reference"])  # fmt: skip
    assert (D(m["claim_amount"]), D(m["allocated_amount"])) == (D("2200.00"), D("2050.00"))
    assert two["matched_claim_lines"] == []
    view = world.api.get(f"/remittances/{world.r1}/reconciliation").json()["data"]
    first = next(x for x in view if x["line"]["id"] == str(world.r1_lines[0]))
    assert first["line"]["matched_claim_lines"] == one["matched_claim_lines"]
    assert first["matches"][0]["allocations"][0]["claim_batch_id"] == world.batch["id"]


def test_unknown_reason_codes_are_the_unmapped_ones_that_arrived(world: World) -> None:
    mfr = str(world.story.manufacturer)
    mapped = world.api.get(f"/manufacturers/{mfr}/reason-code-mappings").json()["data"]
    keep = [{k: m[k] for k in ("external_code", "external_text", "reason_code")} for m in mapped]
    r = world.api.put(
        f"/manufacturers/{mfr}/reason-code-mappings",
        json={"mappings": [*[m for m in keep if m["external_code"] != "E12"],
                           {"external_code": "E12", "reason_code": "rejected_price_mismatch"}]},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    got = world.api.get(f"/manufacturers/{mfr}/unknown-reason-codes").json()["data"]
    assert [(x["external_code"], x["line_count"]) for x in got] == [("ZZ1", 2), ("ZZ2", 1)]
    assert all(x["last_seen_at"] for x in got)
    r = world.api.put(
        f"/manufacturers/{mfr}/reason-code-mappings",
        json={"mappings": [*[m for m in keep if m["external_code"] not in ("E12", "ZZ2")],
                           {"external_code": "E12", "reason_code": "rejected_price_mismatch"},
                           {"external_code": "ZZ2", "reason_code": "rejected_other"}]},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    got = world.api.get(f"/manufacturers/{mfr}/unknown-reason-codes").json()["data"]
    assert [x["external_code"] for x in got] == ["ZZ1"]
    assert world.api.get(f"/manufacturers/{uuid.uuid4()}/unknown-reason-codes").status_code == 404


def _ai_draft(admin: Engine, world: World) -> str:
    """An AI draft waiting for approval (the extraction pipeline needs a model; the owner role inserts the row)."""
    draft = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO agreement_drafts (tenant_id, id, source, status, payload, payload_schema_version, "
                "created_by, submitted_by, submitted_at) VALUES (:t, :d, 'ai_extraction', 'pending_approval', "
                "'{}', 1, :u, :u, now())"
            ),
            {"t": world.story.tenant, "d": draft, "u": world.story.user},
        )
    return str(draft)


def test_home_attention_counts_and_money_found(world: World, admin_engine: Engine) -> None:
    api = world.api
    manual = api.post("/agreement-drafts", json={"source": "manual"})
    assert manual.status_code == 201, manual.text
    ai = _ai_draft(admin_engine, world)
    unclaimed = sum(
        (
            D(s["claim_amount"])
            for s in CALC
            if s["status"] == "eligible" and f"{s['invoice_no']}/{s['line_no']}" not in FIX7
        ),
        D(0),
    )
    assert unclaimed == D("1020.50")  # 11,430.50 eligible - 10,410.00 claimed
    open_unmapped = api.get("/unmapped-entities", params={"status": "open", "limit": 200}).json()["data"]
    waiting = api.get("/imports", params={"status": "awaiting_confirmation", "limit": 200}).json()["data"]

    h = api.get("/home/attention").json()
    assert h["as_of_date"] == "2026-07-10" and h["deadline_within_days"] == 14
    assert (h["imports_waiting"], h["unmapped_open"]) == (len(waiting), len(open_unmapped))
    assert (h["drafts_to_review"], h["drafts_to_review_ai"], h["drafts_to_approve"], h["drafts_to_approve_ai"]) == (
        1, 0, 1, 1)  # fmt: skip
    assert (h["cases_overdue"], h["cases_due_soon"], h["cases_unassigned"]) == (1, 1, 3)
    assert isinstance(h["failed_jobs"], int)  # admin manages jobs
    money = {k: D(v) for k, v in h["money_found"].items()}
    assert money == {
        "unclaimed": D("1020.50"),
        "short_paid_open": D("2230.00"),  # 150.00 + 2,080.00 open, nothing recovered
        "expiring_soon": D("2080.00"),  # of which: SC-07b, due 2026-07-19
        "total": D("3250.50"),  # 1,020.50 + 2,230.00 (expiring is not added again)
    }
    assert world.analyst.get("/home/attention").json()["failed_jobs"] is None

    # deleting the unsubmitted draft takes it off the list; an AI draft pending approval cannot be deleted
    r = api.delete(f"/agreement-drafts/{manual.json()['id']}")
    assert r.status_code == 204, r.text
    assert api.get(f"/agreement-drafts/{manual.json()['id']}").json()["status"] == "cancelled"
    assert "agreement_draft.deleted" in audit_actions(api, manual.json()["id"])
    listed = {d["id"] for d in api.get("/agreement-drafts", params={"limit": 200}).json()["data"]}
    assert manual.json()["id"] not in listed and ai in listed
    cancelled = {d["id"] for d in api.get("/agreement-drafts", params={"status": "cancelled"}).json()["data"]}
    assert manual.json()["id"] in cancelled
    assert api.delete(f"/agreement-drafts/{manual.json()['id']}").status_code == 409
    assert api.delete(f"/agreement-drafts/{ai}").status_code == 409
    approved = api.get("/agreement-drafts", params={"status": "approved"}).json()["data"][0]["id"]
    assert api.delete(f"/agreement-drafts/{approved}").status_code == 409
    assert world.analyst.delete(f"/agreement-drafts/{ai}").status_code == 409  # analysts hold agreement.draft
    assert api.get("/home/attention").json()["drafts_to_review"] == 0
