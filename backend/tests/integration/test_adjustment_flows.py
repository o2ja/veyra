"""S7.4 adjustments end to end against real Postgres, real imports, runs, batches and claim lines (no stand-ins).

Expected values come from ``testdata/generated/ground_truth`` (``expected_calculation.json``,
``claim_batches_fixture.json``, ``expected_rebates.json``, ``expected_changed_rows.json``) or from the hand
derivations in the docstrings. Tests of one scenario share a module-scoped workspace and run in file order."""

import csv
import io
import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from rebate.modules.claims import frozen
from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.integration.agreements_support import approve as approve_draft
from tests.integration.agreements_support import create, member, submit, update
from tests.integration.calc_support import (
    activate_all,
    all_settings,
    claim_and_submit,
    import_sales_part,
    open_workspace,
    results,
    run,
    settings,
)
from tests.integration.conftest import CSV, Workspace, clean_file, import_file, run_import
from tests.integration.missed_reference import assert_same_as_reference
from tests.support import Api, FakeClock, drain, fake_clock, make_app
from tests.testdata import truth

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
SPA_NAMES = ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901", "SPA-BRIT-2026-031", "SPA-COPR-2026-107")
WINDOW = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
FIXTURE = truth("claim_batches_fixture.json")
FIX7 = [f"{x['invoice_no']}/{x['line_no']}" for x in FIXTURE["lines"]]


@dataclass
class Env:
    app: FastAPI
    stack: ExitStack
    clock: FakeClock


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


def workspace_with_versions(
    env: Env, agreements: tuple[str, ...] = SPA_NAMES
) -> tuple[Workspace, dict[str, dict[str, Any]]]:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    return ws, activate_all(ws, agreements)


def workspace(env: Env, agreements: tuple[str, ...] = SPA_NAMES) -> Workspace:
    return workspace_with_versions(env, agreements)[0]


# ---- helpers ------------------------------------------------------------------------------------------------


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def propose(ws: Workspace, run_id: str) -> dict[str, Any]:
    r = ws.api.post(f"/calculation-runs/{run_id}/propose-adjustments")
    assert r.status_code == 200, r.text
    return dict(r.json())


def listing(api: Api, path: str, **params: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        r = api.get(path, params={"limit": 200, **params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        out += r.json()["data"]
        if not r.json()["page"]["has_more"]:
            return out
        cursor = r.json()["page"]["next_cursor"]


def pool(api: Api) -> dict[str, dict[str, Any]]:
    return {f"{x['document_number']}/{x['line_number']}": x for x in listing(api, "/missed-claims")}


def four_eyes(api: Api, on: bool) -> None:
    etag = api.get("/settings").headers["ETag"]
    r = api.patch("/settings", json={"four_eyes_required": on}, headers={"If-Match": etag})
    assert r.status_code == 200, r.text


def audit_actions(api: Api, entity_id: str) -> list[str]:
    r = api.get("/audit-log", params={"entity_id": entity_id, "limit": 200})
    assert r.status_code == 200, r.text
    return [e["action"] for e in r.json()["data"]]


def new_batch(api: Api, mfr: str, **body: Any) -> Any:
    return api.post("/claim-batches", json={"manufacturer_id": mfr, **body}, headers=key())


def outcome(engine: Engine, sql: str, **params: Any) -> str:
    """Run owner-level SQL in its own transaction; the error text, or ``ok``."""
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except DBAPIError as exc:
        return str(exc.orig)
    return "ok"


def file_bytes(env: Env, engine: Engine, file_id: str) -> bytes:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT storage_key FROM file_objects WHERE id = :i"), {"i": file_id}).one()
    return b"".join(storage.read_chunks(env.app.state.settings, row.storage_key))


def csv_rows(env: Env, engine: Engine, api: Api, batch_id: str) -> list[dict[str, str]]:
    r = api.post(f"/claim-batches/{batch_id}/export", json={})
    assert r.status_code == 202, r.text
    drain(env.app)
    rows = list(csv.reader(io.StringIO(file_bytes(env, engine, r.json()["file_id"]).decode())))
    return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


def claim_lines_of(ws: Workspace, batch_id: str) -> dict[str, dict[str, Any]]:
    return {
        x["document_number"] or x["claim_reference"]: x
        for x in listing(ws.api, f"/claim-batches/{batch_id}/lines")
    }


def import_text(ws: Workspace, body: str, name: str) -> dict[str, Any]:
    done = run_import(ws, clean_file(ws, name, body.encode(), CSV), "sales", accept_partial=True)
    assert done["status"] in ("completed", "completed_with_errors"), done
    return done


SALES_HEADER = (
    "invoice_no,line_no,invoice_date,ship_date,customer_code,customer_name,branch,sku,description,qty,uom,"
    "unit_price,unit_cost,ext_amount,spa_ref,project_ref,doc_type,orig_invoice_no,orig_line_no\n"
)


# ---- SC-09r: a return after the claim ----------------------------------------------------------------------


@dataclass
class Returned:
    ws: Workspace
    approver: Api
    analyst: Api
    run_id: str
    results: dict[str, dict[str, Any]]
    first_batch: dict[str, Any]
    proposal: dict[str, Any]


@pytest.fixture(scope="module")
def returned(env: Env) -> Returned:
    """The ground-truth story: the seven lines of CB-VOLT-2026-06 are claimed and submitted (INV-10021/1 at 250.00),
    then the credit note CN-5002 arrives (4 units back) and a new run proposes the reversal."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    first = run(ws, **WINDOW)
    batch = claim_and_submit(ws, results(ws, first["id"]), FIX7)
    import_sales_part(ws, late=True)
    second = run(ws, **WINDOW)
    assert second["status"] == "completed", second
    out = propose(ws, second["id"])
    (proposal,) = out["proposed"]
    return Returned(
        ws,
        member(env.stack, env.app, ws.api, "approver"),
        member(env.stack, env.app, ws.api, "analyst"),
        second["id"],
        results(ws, second["id"]),
        batch,
        proposal,
    )


def test_sc09r_return_after_claim_is_a_return_reversal_proposal(returned: Returned) -> None:
    """GL-S8 / SC-09r: INV-10021/1 was claimed at 250.00 (20 x 12.50); 4 units come back on CN-5002. Hand:
    -(4 x 12.50) = -50.00, the ground truth's ADJ-2026-0001 (return_reversal, -50.00). Nothing else changes: all
    other claimed lines are recalculated to the frozen amounts (no rule change -> no agreement_change)."""
    gt = FIXTURE["adjustments"][0]
    assert Decimal(returned.first_batch["total_amount"]) == Decimal("10410.00")
    r = returned.results["CN-5002/1"]
    assert r["status"] == "adjustment" and Decimal(r["claim_amount"]) == Decimal(gt["amount"])
    p = returned.proposal
    original = claim_lines_of(returned.ws, returned.first_batch["id"])["INV-10021"]
    assert p["adjustment_type"] == gt["type"] == "return_reversal"
    assert Decimal(p["amount"]) == Decimal(gt["amount"]) == Decimal("-50.00")
    assert p["status"] == "proposed" and p["claim_line_id"] == original["id"]
    assert p["calculation_result_id"] == r["id"] and p["run_id"] == returned.run_id
    assert "cannot be net-claimed" not in p["reason"]  # VOLT allows negative claims (master data)
    (only,) = listing(returned.ws.api, "/claim-adjustments")
    assert only["id"] == p["id"] and only["claim_reference"] == original["claim_reference"]


def test_proposals_are_idempotent_per_run_and_never_repeat_across_runs(returned: Returned) -> None:
    ws = returned.ws
    assert propose(ws, returned.run_id)["proposed"] == []
    later = run(ws, **WINDOW)
    out = propose(ws, later["id"])
    assert out["proposed"] == [] and out["compared"] >= 8  # 7 claimed lines + the return, nothing new
    assert len(listing(ws.api, "/claim-adjustments")) == 1


def test_approval_needs_the_right_role_and_a_second_person(returned: Returned) -> None:
    ws, adj = returned.ws, returned.proposal
    four_eyes(ws.api, True)
    try:
        denied = ws.api.post(f"/claim-adjustments/{adj['id']}/approve")  # the proposer approving
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "four_eyes_violation"
        assert "claim_adjustment.four_eyes_violation" in audit_actions(ws.api, adj["id"])
        assert returned.analyst.post(f"/claim-adjustments/{adj['id']}/approve").status_code == 403
        assert returned.analyst.get("/claim-adjustments").status_code == 200  # reading is open
        ok = returned.approver.post(f"/claim-adjustments/{adj['id']}/approve")
        assert ok.status_code == 200 and ok.json()["status"] == "approved"
        assert ok.json()["approved_by"] != adj["proposed_by"] and ok.json()["approved_at"]
        again = returned.approver.post(f"/claim-adjustments/{adj['id']}/approve")
        assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
    finally:
        four_eyes(ws.api, False)
    assert {"claim_adjustment.proposed", "claim_adjustment.approved"} <= set(audit_actions(ws.api, adj["id"]))


def test_batch_with_an_adjustment_ties_out_and_the_adjustment_cannot_be_used_twice(
    returned: Returned, env: Env, admin_engine: Engine
) -> None:
    """Net claimed for VOLT = batch 1 (10,410.00) + adjustment (-50.00) = 10,360.00 = the ground truth's
    batch_total. The adjustment rides in the next batch: INV-10020/1 (SC-08, 312.50) - 50.00 = 262.50 (hand)."""
    ws, adj = returned.ws, returned.proposal
    api = ws.api
    assert Decimal(returned.first_batch["total_amount"]) + Decimal(adj["amount"]) == Decimal(
        FIXTURE["batch_total"]
    )
    volt = adj["manufacturer_id"]
    found = pool(api)
    line = found["INV-10020/1"]
    assert Decimal(line["claim_amount"]) == Decimal("312.50")

    # wrong manufacturer: the adjustment belongs to VOLT
    other = next(v for v in found.values() if v["manufacturer_id"] != volt)
    r = new_batch(api, other["manufacturer_id"], result_ids=[other["id"]], include_adjustment_ids=[adj["id"]])
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "wrong_manufacturer"
    assert api.get("/claim-batches", params={"status": "draft"}).json()["data"] == []  # nothing half-created

    r = new_batch(api, volt, result_ids=[line["id"]], include_adjustment_ids=[adj["id"]])
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["line_count"], Decimal(b["total_amount"])) == (1, Decimal("262.50"))
    (inc,) = listing(api, "/claim-adjustments", included_in_batch_id=b["id"])
    assert inc["id"] == adj["id"] and inc["status"] == "included"
    assert "claim_adjustment.included" in audit_actions(api, adj["id"])

    # double inclusion
    spare = next(v for k, v in pool(api).items() if v["manufacturer_id"] == volt and k != "INV-10020/1")
    dup = new_batch(api, volt, result_ids=[spare["id"]], include_adjustment_ids=[adj["id"]])
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "adjustment_already_included"
    assert dup.json()["error"]["details"][0]["batch_id"] == b["id"]

    # voiding the batch gives the adjustment back
    voided = returned.approver.post(f"/claim-batches/{b['id']}/void", json={"reason": "test"})
    assert voided.status_code == 200
    (freed,) = listing(api, "/claim-adjustments", status="approved")
    assert freed["id"] == adj["id"] and freed["included_in_batch_id"] is None
    assert "claim_adjustment.released" in audit_actions(api, adj["id"])

    # the database ties totals out at commit: attaching the adjustment without moving the total is refused
    plain = new_batch(api, volt, result_ids=[pool(api)["INV-10020/1"]["id"]])
    assert plain.status_code == 201 and Decimal(plain.json()["total_amount"]) == Decimal("312.50")
    got = outcome(
        admin_engine,
        "UPDATE claim_adjustments SET status = 'included', included_in_batch_id = :b WHERE id = :a",
        b=plain.json()["id"],
        a=adj["id"],
    )
    assert "differ from the lines and adjustments" in got, got
    gone = returned.approver.post(f"/claim-batches/{plain.json()['id']}/void", json={"reason": "test"})
    assert gone.status_code == 200

    final = new_batch(
        api, volt, result_ids=[pool(api)["INV-10020/1"]["id"]], include_adjustment_ids=[adj["id"]]
    )
    assert final.status_code == 201, final.text
    fb = final.json()
    assert Decimal(fb["total_amount"]) == Decimal("262.50")
    other_batch = new_batch(api, volt, result_ids=[spare["id"]])
    assert other_batch.status_code == 201, other_batch.text
    rebound = outcome(
        admin_engine,
        "UPDATE claim_adjustments SET included_in_batch_id = :b WHERE id = :a",
        b=other_batch.json()["id"],
        a=adj["id"],
    )
    assert "approval columns change only with the status" in rebound, rebound  # cannot move to another batch
    gone2 = returned.approver.post(f"/claim-batches/{other_batch.json()['id']}/void", json={"reason": "t"})
    assert gone2.status_code == 200
    assert returned.approver.post(f"/claim-batches/{fb['id']}/approve").status_code == 200
    body = csv_rows(env, admin_engine, api, fb["id"])
    assert sum(Decimal(r["Claim amount"]) for r in body) == Decimal(
        "262.50"
    )  # the file ties out to the batch total
    adjustment_row = next(r for r in body if Decimal(r["Claim amount"]) < 0)
    original = claim_lines_of(ws, returned.first_batch["id"])["INV-10021"]
    assert adjustment_row["Claim reference"] == original["claim_reference"]
    assert adjustment_row["Invoice number"] == "CN-5002" and Decimal(
        adjustment_row["Claim amount"]
    ) == Decimal("-50.00")

    done = returned.approver.post(
        f"/claim-batches/{fb['id']}/submit",
        json={"submitted_at": "2026-07-09", "external_reference": "VOLT-ADJ-1"},
        headers=key(),
    )
    assert done.status_code == 200 and done.json()["status"] == "submitted"
    # frozen with the batch: no edit, no release, no delete, even for the owner
    for sql in (
        "UPDATE claim_adjustments SET status = 'approved', included_in_batch_id = NULL WHERE id = :a",
        "UPDATE claim_adjustments SET amount = -1 WHERE id = :a",
        "DELETE FROM claim_adjustments WHERE id = :a",
    ):
        assert outcome(admin_engine, sql, a=adj["id"]) != "ok", sql
    assert api.post(f"/claim-adjustments/{adj['id']}/reject").status_code == 409  # included: too late


# ---- GL-A1: an amendment, recalculated ---------------------------------------------------------------------


def test_gl_a1_amendment_recalculation_proposes_minus_1000(env: Env) -> None:
    """BUSINESS_LOGIC §9.3 example A1: v1 pays 22.00 (100.00 - 78.00); the amendment from 2026-07-01 pays 20.00
    (100.00 - 80.00). INV-A1001/1 (shipped 2026-07-06, q=500) was claimed at 500 x 22.00 = 11,000.00; the
    recalculation proposes 500 x (20.00 - 22.00) = -1,000.00. INV-10051/1 (2026-06-24, 50 x 22.00 = 1,100.00,
    ground truth SC-21) stays on v1a: no proposal."""
    ws, versions = workspace_with_versions(env)
    v1 = versions["SPA-VOLT-2026-884"]
    import_sales_part(ws, late=False)
    import_text(
        ws,
        SALES_HEADER
        + "INV-A1001,1,2026-07-06,2026-07-06,ALDER,Alder Electric LLC (SYNTHETIC),BR01,BRK-3P-100A,"
        "Breaker 3-pole 100A,500,EA,88.00,100.00,44000.00,SPA-VOLT-2026-884,,INV,,\n",
        "a1.csv",
    )
    first = run(ws, **WINDOW)
    res = results(ws, first["id"])
    assert Decimal(res["INV-A1001/1"]["claim_amount"]) == Decimal("11000.00")
    claim_and_submit(ws, res, ["INV-A1001/1", "INV-10051/1"])

    d = create(ws.api, amends_version_id=v1["id"])
    p = d["payload"]
    p["header"]["valid_from"] = "2026-07-01"
    next(i for i in p["items"] if i["sku"] == "BRK-3P-100A")["deviated_cost"] = "80.00"
    d = update(ws.api, d, p).json()
    assert d["validation"]["valid"], d["validation"]
    assert submit(ws.api, d["id"]).status_code == 200
    assert approve_draft(ws.api, d["id"]).status_code == 200

    again = run(ws, **WINDOW)
    res2 = results(ws, again["id"])
    assert Decimal(res2["INV-A1001/1"]["claim_amount"]) == Decimal("10000.00")  # 500 x 20.00
    assert Decimal(res2["INV-10051/1"]["claim_amount"]) == Decimal("1100.00")  # v1a, unchanged
    (adj,) = propose(ws, again["id"])["proposed"]
    assert adj["adjustment_type"] == "agreement_change" and Decimal(adj["amount"]) == Decimal("-1000.00")
    assert "10000" in adj["reason"] and "11000" in adj["reason"]
    assert propose(ws, again["id"])["proposed"] == []

    # a later run says the same: not proposed again while the first is open ...
    third = run(ws, **WINDOW)
    assert propose(ws, third["id"])["proposed"] == []
    # ... nor once it has been approved
    assert ws.api.post(f"/claim-adjustments/{adj['id']}/approve").status_code == 200
    assert propose(ws, third["id"])["proposed"] == []
    # a run that a newer completed run supersedes cannot propose: the pre-amendment run would say +1,000.00
    # (11,000.00 - 11,000.00 - (-1,000.00)), undoing the approved correction
    stale = ws.api.post(f"/calculation-runs/{first['id']}/propose-adjustments")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "invalid_state", stale.text
    assert [Decimal(a["amount"]) for a in listing(ws.api, "/claim-adjustments")] == [Decimal("-1000.00")]
    assert [Decimal(a["amount"]) for a in listing(ws.api, "/claim-adjustments")] == [Decimal("-1000.00")]
    # rejecting an approved one is allowed until it is in a batch; then the difference is open again
    assert ws.api.post(f"/claim-adjustments/{adj['id']}/reject").json()["status"] == "rejected"
    assert ws.api.post(f"/claim-adjustments/{adj['id']}/approve").status_code == 409
    (renew,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(renew["amount"]) == Decimal("-1000.00")


# ---- GL-R1 / GL-R2: rebate claim lines and the clawback ----------------------------------------------------


def test_rebate_accrual_claims_and_the_r2_clawback(env: Env, admin_engine: Engine) -> None:
    """expected_rebates.json: after GL-R1a the ledger shows +14,850.00 and after GL-R1b +10,400.00; the vendor credit
    of GL-R2 gives -10,400.00. Positive deltas are claimed as rebate claim lines, the negative one becomes a
    rebate_clawback adjustment (never a claim line). Batch 2 = the +10,400.00 line and the clawback = 0.00 (hand)."""
    ws = workspace(env, ("REB-VOLT-2026",))
    import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")
    volt = ws.manufacturers["VOLT"]
    gt = {c["id"]: c for c in truth("expected_rebates.json")["cases"]}
    entries: dict[str, dict[str, Any]] = {}
    runs: dict[str, dict[str, Any]] = {}
    for case_id in ("GL-R1a", "GL-R1b", "GL-R2"):
        r = run(ws, manufacturer_ids=[volt], date_from="2026-01-01", date_to=gt[case_id]["as_of"])
        assert r["status"] == "completed", r
        (entries[case_id],) = ws.api.get("/rebate-accruals", params={"run_id": r["id"]}).json()["data"]
        runs[case_id] = r
        assert Decimal(entries[case_id]["delta"]) == Decimal(gt[case_id]["delta"])

    # nothing to propose for the positive deltas
    assert propose(ws, runs["GL-R1a"]["id"])["proposed"] == []
    assert propose(ws, runs["GL-R1b"]["id"])["proposed"] == []

    # GL-R2 -> a clawback proposal for exactly the negative delta
    out = propose(ws, runs["GL-R2"]["id"])
    (claw,) = out["proposed"]
    assert claw["adjustment_type"] == "rebate_clawback" and Decimal(claw["amount"]) == Decimal("-10400.00")
    assert claw["claim_line_id"] is None and claw["status"] == "proposed"
    assert propose(ws, runs["GL-R2"]["id"])["proposed"] == []

    # a negative delta is not a claim line; an unknown entry is not found
    neg = new_batch(ws.api, volt, accrual_entry_ids=[entries["GL-R2"]["id"]])
    assert neg.status_code == 422 and neg.json()["error"]["details"][0]["code"] == "not_claimable"
    unknown = new_batch(ws.api, volt, accrual_entry_ids=[str(uuid.uuid4())])
    assert unknown.status_code == 422 and unknown.json()["error"]["details"][0]["code"] == "not_found"

    # GL-R1a's +14,850.00 as a rebate claim line
    one = new_batch(ws.api, volt, accrual_entry_ids=[entries["GL-R1a"]["id"]])
    assert one.status_code == 201, one.text
    assert (one.json()["line_count"], Decimal(one.json()["total_amount"])) == (1, Decimal("14850.00"))
    assert (one.json()["period_from"], one.json()["period_to"]) == ("2026-01-01", "2026-12-31")
    (line,) = listing(ws.api, f"/claim-batches/{one.json()['id']}/lines")
    assert line["source_kind"] == "rebate_accrual" and line["calculation_result_id"] is None
    assert (
        line["rebate_accrual_entry_id"] == entries["GL-R1a"]["id"] and line["sales_document_line_id"] is None
    )
    assert line["agreement_version_id"] == entries["GL-R1a"]["agreement_version_id"]
    again = new_batch(ws.api, volt, accrual_entry_ids=[entries["GL-R1a"]["id"]])
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_claimed"
    assert again.json()["error"]["details"][0]["claim_reference"] == line["claim_reference"]

    # the clawback must be approved before it can be netted against the next rebate claim
    early = new_batch(
        ws.api, volt, accrual_entry_ids=[entries["GL-R1b"]["id"]], include_adjustment_ids=[claw["id"]]
    )
    assert early.status_code == 422 and early.json()["error"]["details"][0]["code"] == "not_approved"
    assert ws.api.post(f"/claim-adjustments/{claw['id']}/approve").status_code == 200
    # 10,400.00 - 10,400.00 = 0.00 (hand): refused while VOLT does not accept negative claims (owner decision)
    settings(ws, "VOLT", allow_negative_claims=False)
    zero = new_batch(
        ws.api, volt, accrual_entry_ids=[entries["GL-R1b"]["id"]], include_adjustment_ids=[claw["id"]]
    )
    assert zero.status_code == 422 and zero.json()["error"]["code"] == "batch_total_not_positive", zero.text
    assert [a["status"] for a in listing(ws.api, "/claim-adjustments")] == ["approved"]
    assert len(listing(ws.api, "/claim-batches")) == 1  # only the GL-R1a batch
    settings(ws, "VOLT")  # ground truth: VOLT accepts negative claims
    two = new_batch(
        ws.api, volt, accrual_entry_ids=[entries["GL-R1b"]["id"]], include_adjustment_ids=[claw["id"]]
    )
    assert two.status_code == 201, two.text
    assert (two.json()["line_count"], Decimal(two.json()["total_amount"])) == (1, Decimal("0.00"))
    # M1: the current setting also decides at approve and at submit (0.00 <= 0 again)
    bid = two.json()["id"]

    def attempt(step: str) -> Any:
        if step == "approve":
            return ws.api.post(f"/claim-batches/{bid}/approve")
        body = {"submitted_at": "2026-07-09", "external_reference": "REB-1"}
        return ws.api.post(f"/claim-batches/{bid}/submit", json=body, headers=key())

    for step in ("approve", "submit"):
        settings(ws, "VOLT", allow_negative_claims=False)
        refused = attempt(step)
        assert refused.status_code == 422, (step, refused.text)
        assert refused.json()["error"]["code"] == "batch_total_not_positive", (step, refused.text)
        settings(ws, "VOLT")
        if step == "approve":
            assert attempt(step).status_code == 200
    rows = csv_rows(env, admin_engine, ws.api, two.json()["id"])
    assert sorted(Decimal(r["Claim amount"]) for r in rows) == [Decimal("-10400.00"), Decimal("10400.00")]
    assert attempt("submit").status_code == 200


# ---- source_changed (S3 hook) ------------------------------------------------------------------------------


def test_source_change_of_a_claimed_line_is_flagged_and_proposed_not_reclaimed(
    env: Env, admin_engine: Engine
) -> None:
    """expected_changed_rows.json: re-importing sales_corrected_changed_rows.csv changes INV-10001/1 (claimed in
    CB-VOLT-2026-06, price 88.00 -> 87.00) and INV-10002/1 (not claimed, qty 10 -> 12). Only the first is flagged
    source_changed. The new version is price_mismatch (87.00 versus the 88.00 sell price), so the recalculation
    proposes -frozen = -2,200.00 (BUSINESS_LOGIC §9.3), and the line cannot be claimed again."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    first = run(ws, **WINDOW)
    batch = claim_and_submit(ws, results(ws, first["id"]), FIX7)
    gt = truth("expected_changed_rows.json")
    second = import_file(ws, f"sales/{gt['file']}", "sales")
    assert second["counts"]["changed"] == gt["expected"]["changed"] == 2
    assert second["counts"]["unchanged"] == gt["expected"]["unchanged"]
    assert second["counts"]["source_changed"] == 1
    changes = {
        f"{c['document_number']}/{c['line_number']}": c
        for c in listing(ws.api, f"/imports/{second['id']}/changes")
    }
    assert sorted(changes) == gt["expected"]["changed_keys"]
    assert (
        changes["INV-10001/1"]["source_changed"] is True and changes["INV-10002/1"]["source_changed"] is False
    )
    line = ws.api.get(f"/sales-lines/{changes['INV-10001/1']['line_id']}").json()
    assert line["source_changed"] is True  # visible on the line

    run2 = run(ws, **WINDOW)
    res = results(ws, run2["id"])
    assert (res["INV-10001/1"]["status"], res["INV-10001/1"]["exception_code"]) == (
        "exception",
        "price_mismatch",
    )
    (adj,) = [a for a in propose(ws, run2["id"])["proposed"]]
    claim = claim_lines_of(ws, batch["id"])["INV-10001"]
    assert adj["adjustment_type"] == "agreement_change" and adj["claim_line_id"] == claim["id"]
    assert Decimal(adj["amount"]) == -Decimal(claim["claim_amount"]) == Decimal("-2200.00")
    assert "price_mismatch" in adj["reason"]
    assert "INV-10001/1" not in pool(ws.api)


def test_a_changed_claimed_line_that_stays_eligible_is_never_offered_again(
    env: Env, admin_engine: Engine
) -> None:
    """BUSINESS_LOGIC §9.4 and the S3 hook contract, through two versions: INV-10001/1 (claimed at 100 x 22.00 =
    2,200.00) is re-imported twice (qty 90, then qty 80). Both new versions stay eligible (price unchanged), yet the
    missed-claims scan never lists them and a batch refuses them (the claim sits on the first version); the
    recalculation proposes 80 x 22.00 - 2,200.00 = -440.00 against the original claim line."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    first = run(ws, **WINDOW)
    batch = claim_and_submit(ws, results(ws, first["id"]), FIX7)

    def line(qty: int) -> str:
        return (
            "INV-10001,1,2026-06-10,2026-06-10,ALDER,Alder Electric LLC (SYNTHETIC),BR01,BRK-3P-100A,"
            f"Breaker 3-pole 100A,{qty},EA,88.00,100.00,{qty * 88}.00,SPA-VOLT-2026-884,,INV,,\n"
        )

    assert import_text(ws, SALES_HEADER + line(90), "c1.csv")["counts"]["source_changed"] == 1
    assert (
        import_text(ws, SALES_HEADER + line(80), "c2.csv")["counts"]["source_changed"] == 1
    )  # the chain, 2 deep
    run2 = run(ws, **WINDOW)
    res = results(ws, run2["id"])["INV-10001/1"]
    assert res["status"] == "eligible" and Decimal(res["claim_amount"]) == Decimal("1760.00")  # 80 x 22.00
    assert "INV-10001/1" not in pool(ws.api)
    assert_same_as_reference(
        admin_engine, ws.tenant_id
    )  # the superseded-chain walk equals the recursive oracle
    refused = new_batch(ws.api, res["manufacturer_id"], result_ids=[res["id"]])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "already_claimed", refused.text
    assert refused.json()["error"]["details"][0]["batch_number"] == batch["batch_number"]
    assert not [b for b in listing(ws.api, "/claim-batches") if b["id"] != batch["id"]]
    (adj,) = propose(ws, run2["id"])["proposed"]
    claim = claim_lines_of(ws, batch["id"])["INV-10001"]
    assert adj["claim_line_id"] == claim["id"] and Decimal(adj["amount"]) == Decimal("-440.00")
    assert run(ws, **WINDOW)["status"] == "completed"
    assert propose(ws, run(ws, **WINDOW)["id"])["proposed"] == []


def test_a_changed_return_is_corrected_by_the_difference_not_reversed_twice(env: Env) -> None:
    """SC-09r, then CN-5002 is re-imported with 5 units instead of 4. The reversal for the first version is -50.00
    (4 x 12.50); the corrected return is worth -62.50 (5 x 12.50), so exactly one more adjustment of
    -62.50 - (-50.00) = -12.50 is proposed (hand), not a second -62.50."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    batch = claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), FIX7)
    import_sales_part(ws, late=True)
    (first,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(first["amount"]) == Decimal("-50.00")
    import_text(
        ws,
        SALES_HEADER
        + "CN-5002,1,2026-07-02,2026-07-02,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
        "Breaker 3-pole 60A,5,EA,58.00,64.50,290.00,SPA-VOLT-2026-884,,CN,INV-10021,1\n",
        "cn5002.csv",
    )
    run2 = run(ws, **WINDOW)
    assert Decimal(results(ws, run2["id"])["CN-5002/1"]["claim_amount"]) == Decimal("-62.50")
    (extra,) = propose(ws, run2["id"])["proposed"]
    assert extra["adjustment_type"] == "return_reversal" and Decimal(extra["amount"]) == Decimal("-12.50")
    assert (
        extra["claim_line_id"] == first["claim_line_id"] == claim_lines_of(ws, batch["id"])["INV-10021"]["id"]
    )
    assert "-62.50" in extra["reason"] and "-50.00" in extra["reason"] and "-12.50" in extra["reason"]
    assert sum(Decimal(a["amount"]) for a in listing(ws.api, "/claim-adjustments")) == Decimal("-62.50")
    assert propose(ws, run2["id"])["proposed"] == []


def test_propose_serialises_with_other_claim_writes(env: Env, admin_engine: Engine) -> None:
    """Proposals take the per-tenant claims lock (shared with batch creation): while another transaction holds it,
    a propose waits instead of racing it."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), FIX7)
    latest = run(ws, **WINDOW)
    done = threading.Event()

    def call() -> None:
        propose(ws, latest["id"])
        done.set()

    with admin_engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"claims:{ws.tenant_id}"}
        )
        worker = threading.Thread(target=call)
        worker.start()
        assert not done.wait(1.5), "propose ran while the claims lock was held"
    worker.join(30)
    assert done.is_set()


def test_the_database_only_accepts_new_adjustments_as_proposed(
    returned: Returned, admin_engine: Engine
) -> None:
    """Insert guard: an adjustment cannot be born approved or included, even for the owner."""
    p = returned.proposal
    cases = (
        ("approved", ", approved_by, approved_at", ", :u, now()"),
        ("proposed", ", included_in_batch_id", ", :b"),
    )
    for status, cols, vals in cases:
        got = outcome(
            admin_engine,
            "INSERT INTO claim_adjustments (tenant_id, id, manufacturer_id, adjustment_type, claim_line_id, "
            f"amount, reason, status{cols}) VALUES (:t, :id, :m, 'agreement_change', :l, -1, 'x', '{status}'"
            f"{vals})",
            t=returned.ws.tenant_id,
            id=str(uuid.uuid4()),
            m=p["manufacturer_id"],
            l=p["claim_line_id"],
            u=p["proposed_by"],
            b=returned.first_batch["id"],
        )
        assert "created as proposed" in got, (status, got)


def test_a_manufacturer_without_negative_claims_blocks_the_return_reversal(env: Env) -> None:
    """BUSINESS_LOGIC §7 as written: with allow_negative_claims = false the reversal stays proposed ("cannot be
    net-claimed; discuss with the manufacturer"), cannot be approved, and cannot be included in a batch."""
    ws = workspace(env)
    settings(ws, "VOLT", allow_negative_claims=False)
    import_sales_part(ws, late=False)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), FIX7)
    import_sales_part(ws, late=True)
    (adj,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(adj["amount"]) == Decimal("-50.00") and "cannot be net-claimed" in adj["reason"]
    blocked = ws.api.post(f"/claim-adjustments/{adj['id']}/approve")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "cannot_net_claim", blocked.text
    assert listing(ws.api, "/claim-adjustments")[0]["status"] == "proposed"
    # approved while the manufacturer allowed it, then the setting is withdrawn: inclusion is refused all the same
    settings(ws, "VOLT", allow_negative_claims=True)
    assert ws.api.post(f"/claim-adjustments/{adj['id']}/approve").status_code == 200
    settings(ws, "VOLT", allow_negative_claims=False)
    line = pool(ws.api)["INV-10020/1"]
    refused = new_batch(
        ws.api, line["manufacturer_id"], result_ids=[line["id"]], include_adjustment_ids=[adj["id"]]
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "cannot_net_claim", refused.text
    assert listing(ws.api, "/claim-adjustments")[0]["status"] == "approved"
    assert [b["status"] for b in listing(ws.api, "/claim-batches")] == ["submitted"]


def test_a_draft_batch_is_not_a_frozen_claim(env: Env) -> None:
    """INV-10021/1 (20 x 12.50 = 250.00) sits in a DRAFT batch when CN-5002 returns 4 units. Hand: nothing is frozen,
    so the return nets the line (16 x 12.50 = 200.00) and no reversal is proposed. After the draft is voided the
    line is claimed at 200.00, not 250.00 - 50.00 netted twice (150.00)."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    first = results(ws, run(ws, **WINDOW)["id"])["INV-10021/1"]
    draft = new_batch(ws.api, first["manufacturer_id"], result_ids=[first["id"]])
    assert draft.status_code == 201 and Decimal(draft.json()["total_amount"]) == Decimal("250.00"), draft.text
    import_sales_part(ws, late=True)
    second = run(ws, **WINDOW)
    assert Decimal(results(ws, second["id"])["INV-10021/1"]["claim_amount"]) == Decimal("200.00")
    assert propose(ws, second["id"])["proposed"] == []
    assert (
        ws.api.post(f"/claim-batches/{draft.json()['id']}/void", json={"reason": "test"}).status_code == 200
    )
    third = results(ws, run(ws, **WINDOW)["id"])["INV-10021/1"]
    assert Decimal(third["claim_amount"]) == Decimal("200.00")
    again = new_batch(ws.api, third["manufacturer_id"], result_ids=[third["id"]])
    assert again.status_code == 201 and Decimal(again.json()["total_amount"]) == Decimal("200.00"), again.text


def test_the_database_enforces_negative_claims(env: Env, admin_engine: Engine) -> None:
    """Owner decision 1 below the service: a batch totalling 0.00 (hand: 0 <= 0; refused before the line tie-out) and a negative
    return reversal's approval are refused while the manufacturer's allow_negative_claims is false, and accepted
    (positive control) once it is true."""
    ws = workspace(env)
    settings(ws, "VOLT", allow_negative_claims=False)
    import_sales_part(ws, late=False)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), FIX7)
    import_sales_part(ws, late=True)
    (adj,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    user = adj["proposed_by"]
    batch = (
        "INSERT INTO claim_batches (tenant_id, id, manufacturer_id, batch_number, line_count, total_amount, "
        "created_by) VALUES (:t, gen_random_uuid(), :m, :n, 1, 0, :u)"
    )
    approve_sql = "UPDATE claim_adjustments SET status = 'approved', approved_by = :u, approved_at = now() WHERE id = :a"
    args = {"t": ws.tenant_id, "m": adj["manufacturer_id"], "u": user, "a": adj["id"]}
    assert "allows negative claims" in outcome(admin_engine, batch, n="CB-ZERO-1", **args)
    assert "does not accept negative claims" in outcome(admin_engine, approve_sql, **args)
    settings(ws, "VOLT", allow_negative_claims=True)
    assert "allows negative claims" not in outcome(admin_engine, batch, n="CB-ZERO-2", **args)
    assert outcome(admin_engine, approve_sql, **args) == "ok"


def test_the_database_refuses_including_an_adjustment_of_an_unfrozen_line(
    env: Env, admin_engine: Engine
) -> None:
    """INV-10021/1 sits in a DRAFT batch, so its claim line is not frozen. An approved adjustment of that line cannot be
    included in a second draft batch (even for the owner): the corrected line must be frozen and not released."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    res = results(ws, run(ws, **WINDOW)["id"])
    first = res["INV-10021/1"]
    mfr = first["manufacturer_id"]
    draft = new_batch(ws.api, mfr, result_ids=[first["id"]])
    assert draft.status_code == 201, draft.text
    other = new_batch(ws.api, mfr, result_ids=[res["INV-10020/1"]["id"]])
    assert other.status_code == 201, other.text
    adj, user = str(uuid.uuid4()), draft.json()["created_by"]
    args = {"t": ws.tenant_id, "a": adj, "m": mfr, "u": user, "b": other.json()["id"]}
    args["l"] = next(iter(claim_lines_of(ws, draft.json()["id"]).values()))["id"]
    steps = (
        "INSERT INTO claim_adjustments (tenant_id, id, manufacturer_id, adjustment_type, claim_line_id, amount, reason, "
        "status) VALUES (:t, :a, :m, 'agreement_change', :l, -1, 'x', 'proposed')",
        "UPDATE claim_adjustments SET status = 'approved', approved_by = :u, approved_at = now() WHERE id = :a",
    )
    assert [outcome(admin_engine, sql, **args) for sql in steps] == ["ok", "ok"]
    include_sql = "UPDATE claim_adjustments SET status = 'included', included_in_batch_id = :b WHERE id = :a"
    assert "frozen and not released" in outcome(admin_engine, include_sql, **args)


# ---- owner decision 2026-11-13: return before claim; an adjustment belongs to the batch that carries it -------


CN_5003 = (
    "CN-5003,1,2026-07-03,2026-07-03,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
    "Breaker 3-pole 60A,3,EA,58.00,64.50,174.00,SPA-VOLT-2026-884,,CN,INV-10021,1\n"
)


def test_return_before_claim_then_a_second_return_after_the_claim(env: Env) -> None:
    """D-N1. CN-5002 (4 units) is imported BEFORE INV-10021/1 is claimed. Hand: 20 - 4 = 16 units x 12.50 = 200.00.
    The claim freezes at 200.00; the re-run after the claim still says 200.00 (not 250.00), CN-5002 stays netted
    (0.00) and nothing is proposed (no agreement_change +50.00, no return_reversal -50.00). A second return CN-5003
    of 3 units after the claim is reversed at the frozen unit amount: -(3 x 12.50) = -37.50, and only that."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    import_sales_part(ws, late=True)
    first = results(ws, run(ws, **WINDOW)["id"])
    assert Decimal(first["INV-10021/1"]["claim_amount"]) == Decimal("200.00")
    assert first["CN-5002/1"]["status"] == "netted"
    batch = claim_and_submit(ws, first, ["INV-10021/1"])
    assert Decimal(batch["total_amount"]) == Decimal("200.00")

    second = run(ws, **WINDOW)
    again = results(ws, second["id"])
    assert again["INV-10021/1"]["status"] == "eligible"
    assert Decimal(again["INV-10021/1"]["claim_amount"]) == Decimal("200.00")
    assert again["CN-5002/1"]["status"] == "netted" and Decimal(again["CN-5002/1"]["claim_amount"]) == 0
    assert propose(ws, second["id"])["proposed"] == []

    import_text(ws, SALES_HEADER + CN_5003, "cn5003.csv")
    third = run(ws, **WINDOW)
    res = results(ws, third["id"])
    assert res["CN-5002/1"]["status"] == "netted"
    assert res["CN-5003/1"]["status"] == "adjustment"
    assert Decimal(res["CN-5003/1"]["claim_amount"]) == Decimal("-37.50")
    (adj,) = propose(ws, third["id"])["proposed"]
    assert adj["adjustment_type"] == "return_reversal" and Decimal(adj["amount"]) == Decimal("-37.50")
    assert adj["claim_line_id"] == claim_lines_of(ws, batch["id"])["INV-10021"]["id"]


def test_an_adjustment_is_counted_in_the_batch_that_carries_it(env: Env) -> None:
    """B1 holds INV-10021/1 frozen at 200.00 (hand: 16 x 12.50); CN-5003 (3 units) later gives the approved
    adjustment -37.50 (3 x 12.50). B2 carries it with INV-10020/1 (312.50 = (30 - 5) x 12.50):
    B2 = 312.50 - 37.50 = 275.00. B1 stays 200.00 and its line keeps its frozen amount."""
    ws = workspace(env)
    approver = member(env.stack, env.app, ws.api, "approver")
    import_sales_part(ws, late=False)
    import_sales_part(ws, late=True)
    b1 = claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_text(ws, SALES_HEADER + CN_5003, "cn5003.csv")
    (adj,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert approver.post(f"/claim-adjustments/{adj['id']}/approve").status_code == 200
    line = pool(ws.api)["INV-10020/1"]
    assert Decimal(line["claim_amount"]) == Decimal("312.50")
    r = new_batch(
        ws.api, line["manufacturer_id"], result_ids=[line["id"]], include_adjustment_ids=[adj["id"]]
    )
    assert r.status_code == 201, r.text
    b2 = r.json()
    assert Decimal(b2["total_amount"]) == Decimal("275.00")
    held = ws.api.get(f"/claim-batches/{b1['id']}").json()
    assert Decimal(held["total_amount"]) == Decimal("200.00") == Decimal(b1["total_amount"])
    assert Decimal(claim_lines_of(ws, b1["id"])["INV-10021"]["claim_amount"]) == Decimal("200.00")


def test_a_netted_return_later_reduced_proposes_the_shortfall_as_an_agreement_change(env: Env) -> None:
    """D-N10. INV-10021/1 (20 x 12.50) is claimed at 200.00 = (20 - 4) x 12.50, net of CN-5002's 4 units. CN-5002 is
    then re-imported with 2 units. Hand: the original is now worth (20 - 2) x 12.50 = 225.00, so exactly
    225.00 - 200.00 = +25.00 is proposed as an agreement_change on the claim line; the frozen claim stays 200.00 and
    CN-5002 (2 units, all within the 4 netted) stays netted, no return_reversal."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    import_sales_part(ws, late=True)
    batch = claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_text(
        ws,
        SALES_HEADER
        + "CN-5002,1,2026-07-02,2026-07-02,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
        "Breaker 3-pole 60A,2,EA,58.00,64.50,116.00,SPA-VOLT-2026-884,,CN,INV-10021,1\n",
        "cn5002_reduced.csv",
    )
    second = run(ws, **WINDOW)
    res = results(ws, second["id"])
    assert Decimal(res["INV-10021/1"]["claim_amount"]) == Decimal("225.00")
    assert res["CN-5002/1"]["status"] == "netted"
    (adj,) = propose(ws, second["id"])["proposed"]
    assert adj["adjustment_type"] == "agreement_change" and Decimal(adj["amount"]) == Decimal("25.00")
    line = claim_lines_of(ws, batch["id"])["INV-10021"]
    assert adj["claim_line_id"] == line["id"] and Decimal(line["claim_amount"]) == Decimal("200.00")
    assert propose(ws, second["id"])["proposed"] == []


# ---- a reversal is undone when its return is no longer open (second-pass review) -----------------------------

CN_5002_UNLINKED = (
    "CN-5002,1,2026-07-02,2026-07-02,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
    "Breaker 3-pole 60A,4,EA,58.00,64.50,232.00,SPA-VOLT-2026-884,,CN,,\n"
)


def _total(ws: Workspace) -> Decimal:
    return sum((Decimal(a["amount"]) for a in listing(ws.api, "/claim-adjustments")), Decimal(0))


def test_a_reversal_is_undone_when_a_netted_return_is_reduced(env: Env) -> None:
    """Claim 200.00 = (20 - 4) x 12.50 net of CN-5002 (N = 4). CN-5003 (3 units) after the claim: -(3 x 12.50) =
    -37.50. CN-5002 is then reduced to 1 unit: returns R = 1 + 3 = 4 = N, so the original stays (20 - 4) x 12.50 =
    200.00 and CN-5003 is open for max(1 + 3 - 4, 0) = 0 units: reversal due 0, so +37.50 undoes it (total 0)."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    import_sales_part(ws, late=True)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_text(ws, SALES_HEADER + CN_5003, "cn5003.csv")
    (first,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(first["amount"]) == Decimal("-37.50")
    import_text(
        ws,
        SALES_HEADER
        + "CN-5002,1,2026-07-02,2026-07-02,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
        "Breaker 3-pole 60A,1,EA,58.00,64.50,58.00,SPA-VOLT-2026-884,,CN,INV-10021,1\n",
        "cn5002_one.csv",
    )
    second = run(ws, **WINDOW)
    assert Decimal(results(ws, second["id"])["INV-10021/1"]["claim_amount"]) == Decimal("200.00")
    (undo,) = propose(ws, second["id"])["proposed"]
    assert undo["adjustment_type"] == "return_reversal" and Decimal(undo["amount"]) == Decimal("37.50")
    assert _total(ws) == 0
    assert propose(ws, second["id"])["proposed"] == []
    assert propose(ws, run(ws, **WINDOW)["id"])["proposed"] == []


def test_a_voided_netted_return_shifts_the_basis_and_undoes_the_later_reversal(env: Env) -> None:
    """Same claim (200.00, N = 4) and CN-5003 reversal -37.50. CN-5002 is then unlinked (voided): R = 3, so the
    original is (20 - min(4, 3)) x 12.50 = 17 x 12.50 = 212.50 (+12.50 agreement_change) and CN-5003 is open for
    max(0 + 3 - 4, 0) = 0 units: +37.50 undoes the reversal. Cumulative: -37.50 + 37.50 + 12.50 = +12.50, i.e. the
    claim 200.00 + 12.50 = 212.50 = 17 x 12.50."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    import_sales_part(ws, late=True)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_text(ws, SALES_HEADER + CN_5003, "cn5003.csv")
    propose(ws, run(ws, **WINDOW)["id"])
    import_text(ws, SALES_HEADER + CN_5002_UNLINKED, "cn5002_void.csv")
    second = run(ws, **WINDOW)
    assert Decimal(results(ws, second["id"])["INV-10021/1"]["claim_amount"]) == Decimal("212.50")
    made = propose(ws, second["id"])["proposed"]
    by_type = {a["adjustment_type"]: Decimal(a["amount"]) for a in made}
    assert by_type == {"agreement_change": Decimal("12.50"), "return_reversal": Decimal("37.50")}
    assert _total(ws) == Decimal("12.50")
    assert propose(ws, second["id"])["proposed"] == []


def test_a_reversal_is_undone_when_its_return_is_voided(env: Env) -> None:
    """SC-09r: claimed 250.00 before CN-5002 exists, reversal -4 x 12.50 = -50.00. CN-5002 is then unlinked (voided):
    the original stays 250.00 (no agreement_change) and the reversal is undone by +50.00 (total 0)."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_sales_part(ws, late=True)
    (first,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(first["amount"]) == Decimal("-50.00")
    import_text(ws, SALES_HEADER + CN_5002_UNLINKED, "cn5002_void.csv")
    second = run(ws, **WINDOW)
    (undo,) = propose(ws, second["id"])["proposed"]
    assert undo["adjustment_type"] == "return_reversal" and Decimal(undo["amount"]) == Decimal("50.00")
    assert undo["claim_line_id"] == first["claim_line_id"] and _total(ws) == 0
    assert propose(ws, second["id"])["proposed"] == []


def test_the_netting_basis_of_a_claim_reads_engine_1_0_0_traces_and_fails_loudly_otherwise(
    admin_engine: Engine,
) -> None:
    """The SQL fragment that reads N from a claim's trace, run against literal traces (no writes): a 1.0.0 trace
    without ``netted_returned_quantity`` falls back to ``returned_quantity`` (4); the same trace labelled 1.1.0 gives
    NULL (the reader then raises); a 1.1.0 trace with the key gives 4."""

    def basis(version: str, **keys: str) -> Decimal | None:
        step = {"step": "returns", "returned_quantity": "4", **keys}
        trace = json.dumps({"engine_version": version, "steps": [{"step": "price_check"}, step]})
        with admin_engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT {frozen._NETTED} AS n FROM (SELECT CAST(:t AS jsonb) AS trace) r"),
                {"t": trace},
            ).one()
        return None if row.n is None else Decimal(row.n)

    assert basis("1.0.0") == Decimal("4")
    assert basis("1.1.0") is None
    assert basis("1.1.0", netted_returned_quantity="4") == Decimal("4")


def test_a_return_relinked_to_another_original_moves_its_reversal(env: Env) -> None:
    """INV-10021/1 and INV-10020/1 are claimed (250.00 = 20 x 12.50; 312.50 = (30 - 5) x 12.50, N = 5 for CN-5001).
    CN-5002 (4 units) arrives: -(4 x 12.50) = -50.00 on INV-10021. It is then re-imported against INV-10020/1: that
    original's returns before it are CN-5001 (5), so it is open for min(4, 5 + 4 - 5) = 4 units = -50.00 on INV-10020,
    and INV-10021 (no return left, 250.00 as frozen) gets +50.00. Total 0, no double reversal."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1", "INV-10020/1"])
    import_sales_part(ws, late=True)
    (first,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(first["amount"]) == Decimal("-50.00")
    import_text(
        ws,
        SALES_HEADER + CN_5002_UNLINKED.replace("CN,,\n", "CN,INV-10020,1\n"),
        "cn5002_relinked.csv",
    )
    second = run(ws, **WINDOW)
    made = propose(ws, second["id"])["proposed"]
    by_line = {a["claim_line_id"]: Decimal(a["amount"]) for a in made}
    assert len(made) == 2 and sorted(by_line.values()) == [Decimal("-50.00"), Decimal("50.00")]
    assert by_line[first["claim_line_id"]] == Decimal("50.00")
    assert _total(ws) == Decimal("-50.00")  # -50.00 (first) + 50.00 (undo) - 50.00 (INV-10020)
    assert propose(ws, second["id"])["proposed"] == []


def test_an_older_run_cannot_unwind_a_later_reversal(env: Env) -> None:
    """N = 4 (claim 200.00). R1 (July window, CN-5002 reduced to 1 unit) has CN-5003 netted: max(1 + 3 - 4, 0) = 0.
    CN-5002 back to 4 units: R2 has CN-5003 open for min(3, 4 + 3 - 4) = 3 units = -37.50, proposed from R2.
    Proposing from the older R1 would add +37.50 and wipe it out: refused with 409 invalid_state."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    import_sales_part(ws, late=True)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_text(ws, SALES_HEADER + CN_5003, "cn5003.csv")
    cn5002 = (
        "CN-5002,1,2026-07-02,2026-07-02,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
        "Breaker 3-pole 60A,{q},EA,58.00,64.50,58.00,SPA-VOLT-2026-884,,CN,INV-10021,1\n"
    )
    july = {"date_from": "2026-07-01", "date_to": "2026-12-31"}
    import_text(ws, SALES_HEADER + cn5002.format(q=1), "cn5002_one.csv")
    older = run(ws, **july)
    assert results(ws, older["id"])["CN-5003/1"]["status"] == "netted"
    import_text(ws, SALES_HEADER + cn5002.format(q=4), "cn5002_four.csv")
    newer = run(ws, **july)
    (adj,) = propose(ws, newer["id"])["proposed"]
    assert Decimal(adj["amount"]) == Decimal("-37.50")
    stale = ws.api.post(f"/calculation-runs/{older['id']}/propose-adjustments")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "invalid_state", stale.text
    assert _total(ws) == Decimal("-37.50")


def test_a_reversed_return_that_becomes_an_exception_keeps_its_reversal(env: Env) -> None:
    """SC-09r: claimed 250.00 (N = 0), CN-5002 (4 units) reversed -50.00 and approved. A 17-unit return CN-5009 of the
    same original arrives: R = 4 + 17 = 21 > 20 units, so both returns are return_exceeds_original exceptions. A
    return that is an exception is not gone: nothing is unwound (no +50.00) and nothing new is reversed. Removing the
    17-unit return (unlinking it) makes CN-5002 open again for 4 units: due -50.00 = done, nothing new; total -50.00."""
    ws = workspace(env)
    approver = member(env.stack, env.app, ws.api, "approver")
    import_sales_part(ws, late=False)
    claim_and_submit(ws, results(ws, run(ws, **WINDOW)["id"]), ["INV-10021/1"])
    import_sales_part(ws, late=True)
    (first,) = propose(ws, run(ws, **WINDOW)["id"])["proposed"]
    assert Decimal(first["amount"]) == Decimal("-50.00")
    assert approver.post(f"/claim-adjustments/{first['id']}/approve").status_code == 200
    cn = (
        "CN-5009,1,2026-07-04,2026-07-04,BLUFF,Bluff City Mechanical (SYNTHETIC),BR01,BRK-3P-60A,"
        "Breaker 3-pole 60A,17,EA,58.00,64.50,986.00,SPA-VOLT-2026-884,,CN,{orig}\n"
    )
    import_text(ws, SALES_HEADER + cn.format(orig="INV-10021,1"), "cn5009.csv")
    over = run(ws, **WINDOW)
    res = results(ws, over["id"])
    assert (
        res["CN-5002/1"]["exception_code"] == res["CN-5009/1"]["exception_code"] == "return_exceeds_original"
    )
    assert propose(ws, over["id"])["proposed"] == []
    import_text(ws, SALES_HEADER + cn.format(orig=","), "cn5009_unlinked.csv")
    fixed = run(ws, **WINDOW)
    assert results(ws, fixed["id"])["CN-5002/1"]["status"] == "adjustment"
    assert propose(ws, fixed["id"])["proposed"] == []
    assert _total(ws) == Decimal("-50.00")
