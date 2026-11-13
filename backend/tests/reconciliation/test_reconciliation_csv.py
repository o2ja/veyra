"""S8 remittance CM-7781 (CSV) end to end on real Postgres: ingest -> matching -> decisions -> finalisation, against
``testdata/generated/ground_truth/expected_reconciliation.json``. Money in the expectations is the ground truth or a
hand derivation, never the output of the code under test. Tests of the scenario share a module-scoped workspace and
run in file order."""

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.modules import reconciliation
from rebate.modules.masterdata import Who
from rebate.modules.reconciliation import FinalisedRemittance, matching
from tests.conftest import DbUrls
from tests.reconciliation.support import (
    CSV,
    EXPECTED,
    Story,
    amounts,
    build_story,
    check_findings,
    check_matching,
    create,
    finalize,
    ingest,
    key,
    listing_of,
    reconcile,
    upload,
    view,
)
from tests.support import FakeClock, drain, fake_clock, make_app
from tests.testdata import data

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
CM_CSV = data("remittance/credit_memo_VOLT_CM-7781.csv")
GT = EXPECTED["expected"]
SHARED: dict[str, Any] = {}  # the CM-7781 remittance, shared by the tests that follow its ingestion


class Env:
    def __init__(self, app: FastAPI, stack: ExitStack, clock: FakeClock) -> None:
        self.app, self.stack, self.clock = app, stack, clock


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


@pytest.fixture(scope="module")
def story(env: Env) -> Story:
    return build_story(env.app, env.stack)


def variant(extra: str) -> bytes:
    """Same lines as the ground-truth file with a harmless trailing remark, so the content hash differs."""
    return CM_CSV.replace(b"Credit - unidentified", b"Credit - unidentified" + extra.encode())


# ---- ambiguity: never auto-confirmed -----------------------------------------------------------------------


def test_two_payment_lines_for_one_claim_line_are_never_auto_confirmed(story: Story) -> None:
    """Two lines both name INV-10001/1 (claim CL-1, 2,200.00); each is 1,000.00 (hand total 2,000.00). The invoice-line
    tier is unambiguous from neither side, so nothing may be confirmed without a person."""
    ws, volt = story.ws, story.mfr["VOLT"]
    body = (
        "line,invoice_no,invoice_line,part_no,qty,amount_claimed,amount_paid,reason_code,reason_text\n"
        "1,INV-10001,1,BRK-3P-100A,100,2200.00,1000.00,,\n"
        "2,INV-10001,1,BRK-3P-100A,100,2200.00,1000.00,,\n"
    )
    doc = ingest(
        ws, upload(ws, "amb.csv", body.encode(), CSV), volt, document_number="AMB-1", total_amount="2000.00"
    )
    assert doc["status"] == "accepted" and Decimal(doc["total_amount"]) == Decimal("2000.00")
    reconcile(ws, doc["id"])
    rows = view(ws, doc["id"])
    statuses = [m["status"] for r in rows.values() for m in r["matches"]]
    assert "confirmed" not in statuses
    assert all(r["state"] in ("suggested", "unmatched") for r in rows.values())
    for r in rows.values():
        for m in r["matches"]:
            assert m["status"] == "suggested" and m["method"] in ("fuzzy", "subset_sum")


# ---- arithmetic check: refuse acceptance on a mismatch ----------------------------------------------------------


def test_a_stated_total_that_does_not_equal_the_lines_refuses_acceptance_until_corrected(
    story: Story,
) -> None:
    """Hand sum of the paid column: 2050 + 3120 + 0 + 200 + 2100 + 75 = 7545.00. A stated total of 7545.50 differs by
    0.50 (> 0.01): the document waits in review, acceptance is refused with the difference, and a corrected total
    (or only then) lets it through. Nothing is matchable before that."""
    ws, volt = story.ws, story.mfr["VOLT"]
    doc = ingest(
        ws,
        upload(ws, "cm-mismatch.csv", variant(" (a)"), CSV),
        volt,
        document_number="CM-7781-A",
        total_amount="7545.50",
    )
    assert doc["status"] == "pending_review"
    assert ws.api.get(f"/remittances/{doc['id']}/lines").json()["data"] == []  # not matchable
    ex = ws.api.get(f"/remittances/{doc['id']}/extraction").json()
    assert [p["code"] for p in ex["problems"]] == ["total_mismatch"]
    assert "7545.00" in ex["problems"][0]["message"] and "7545.50" in ex["problems"][0]["message"]
    assert len(ex["lines"]) == 6
    refused = ws.api.post(f"/remittances/{doc['id']}/accept", json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "cannot_accept"
    assert refused.json()["error"]["details"][0]["code"] == "total_mismatch"
    assert reconcile_refused(ws, doc["id"])  # an unaccepted remittance cannot be reconciled
    ok = ws.api.post(f"/remittances/{doc['id']}/accept", json={"total_amount": "7545.00"})
    assert ok.status_code == 200 and ok.json()["status"] == "accepted"
    assert len(ws.api.get(f"/remittances/{doc['id']}/lines").json()["data"]) == 6


def reconcile_refused(ws: Any, remittance_id: str) -> bool:
    r = ws.api.post(f"/remittances/{remittance_id}/reconcile")
    return bool(r.status_code == 409 and r.json()["error"]["code"] == "invalid_state")


def test_a_correction_that_still_does_not_add_up_stays_refused_and_a_negative_payment_is_flagged(
    story: Story,
) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    doc = ingest(
        ws,
        upload(ws, "cm-b.csv", variant(" (b)"), CSV),
        volt,
        document_number="CM-7781-B",
        total_amount="7545.50",
    )
    r = ws.api.post(
        f"/remittances/{doc['id']}/accept",
        json={"corrections": [{"line_number": 6, "amount_paid": "-75.00"}]},
    )
    assert r.status_code == 409
    codes = sorted(d["code"] for d in r.json()["error"]["details"])
    assert codes == ["negative_amount", "total_mismatch"]  # corrections are kept (visible on the next read)
    line6 = ws.api.get(f"/remittances/{doc['id']}/extraction").json()["lines"][5]
    assert Decimal(line6["amount_paid"]) == Decimal("-75.00") and line6["corrected"] is True
    assert ws.api.post(f"/remittances/{doc['id']}/void").status_code == 200
    assert ws.api.post(f"/remittances/{doc['id']}/accept", json={}).status_code == 409  # voided: final


# ---- ingestion of the ground-truth CSV -------------------------------------------------------------------------


def test_the_credit_memo_csv_is_ingested_and_accepted(story: Story, admin_engine: Engine) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    doc = ingest(
        ws,
        upload(ws, "credit_memo_VOLT_CM-7781.csv", CM_CSV, CSV),
        volt,
        document_number="CM-7781",
        document_date="2026-07-31",
        payment_date="2026-07-31",
        total_amount=EXPECTED["document_total"],
    )
    assert doc["status"] == "accepted" and doc["channel"] == "credit_memo"
    assert Decimal(doc["total_amount"]) == Decimal(EXPECTED["document_total"]) == Decimal("7545.00")
    lines = listing_of(ws, f"/remittances/{doc['id']}/lines")
    assert [Decimal(x["amount_paid"]) for x in lines] == [
        Decimal(v) for v in ("2050.00", "3120.00", "0.00", "200.00", "2100.00", "75.00")
    ]
    assert sum(Decimal(x["amount_paid"]) for x in lines) == Decimal("7545.00")
    assert [x["line_number"] for x in lines] == [1, 2, 3, 4, 5, 6]
    assert lines[0]["invoice_number"] == "INV-10001" and lines[0]["invoice_line_number"] == 1
    assert lines[0]["external_reason_code"] == "E12" and lines[0]["reason_code"] == "rejected_price_mismatch"
    assert lines[2]["external_reason_code"] == "E07"
    assert lines[2]["reason_code"] == "rejected_quantity_exceeds_authorization"
    assert lines[1]["reason_code"] is None and lines[4]["claim_reference"] is None
    # evidence: the staging rows are kept with the document
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM remittance_extraction_staging WHERE remittance_document_id = :d"),
            {"d": doc["id"]},
        ).scalar_one()
    assert n == 6
    SHARED["doc"] = doc


def test_a_second_remittance_with_the_same_document_number_is_refused(story: Story) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    again = create(ws, upload(ws, "cm-dup.csv", variant(" (dup)"), CSV), volt, document_number="CM-7781")
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate_remittance"
    # the same file again is the files module's duplicate (409), and a number taken from the file is checked in the job
    dn = b"notice,invoice_no,invoice_line,deduction_amount,reason\nCM-7781,INV-10001,1,10.00,x\n"
    doc = ingest(ws, upload(ws, "dn-dup.csv", dn, CSV), volt, total_amount="10.00")
    assert doc["status"] == "void"  # document number CM-7781 read from the file already exists for VOLT


def test_the_same_file_cannot_back_two_remittances(story: Story) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    doc = SHARED["doc"]
    r = create(ws, doc["file_id"], volt, document_number="CM-7781-Z")
    assert r.status_code == 409


# ---- the matching run ------------------------------------------------------------------------------------------


def test_matching_reproduces_the_ground_truth(story: Story) -> None:
    doc = SHARED["doc"]
    reconcile(story.ws, doc["id"])
    check_matching(story, view(story.ws, doc["id"]))


def test_a_rerun_keeps_confirmed_matches_untouched(story: Story) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    before = view(ws, doc["id"])
    confirmed = {n: r["matches"][0] for n, r in before.items() if r["state"] == "matched"}
    assert sorted(confirmed) == [1, 2, 3, 4]
    reconcile(ws, doc["id"])
    after = view(ws, doc["id"])
    for n, m in confirmed.items():
        assert after[n]["matches"] == [m], n  # same match id, revision, allocations, evidence
    assert after[5]["state"] == "suggested" and after[6]["state"] == "unmatched"
    assert len(after[5]["matches"]) == 1


# ---- decisions -------------------------------------------------------------------------------------------------


def test_confirming_needs_the_revision_and_a_suggestion(story: Story) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    m = view(ws, doc["id"])[5]["matches"][0]
    assert ws.api.post(f"/matches/{m['id']}/confirm").status_code == 412  # no If-Match
    assert ws.api.post(f"/matches/{m['id']}/confirm", headers={"If-Match": '"99"'}).status_code == 412
    done = ws.api.post(f"/matches/{m['id']}/confirm", headers={"If-Match": f'"{m["revision"]}"'})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "confirmed" and done.json()["confirmed_by"] == ws.user_id
    assert done.headers["ETag"] == f'"{m["revision"] + 1}"'
    assert sorted(amounts(done.json())) == [Decimal("1000.00"), Decimal("1100.00")]
    again = ws.api.post(f"/matches/{m['id']}/reject", headers={"If-Match": done.headers["ETag"]})
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
    got = ws.api.get(f"/matches/{m['id']}")
    assert got.status_code == 200 and got.json()["status"] == "confirmed"
    # a confirmed match survives another run
    reconcile(ws, doc["id"])
    assert view(ws, doc["id"])[5]["matches"] == [got.json()]


def test_match_listing_is_paged_and_filtered(story: Story) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    r1 = ws.api.get("/matches", params={"remittance_id": doc["id"], "limit": 2})
    assert r1.status_code == 200 and len(r1.json()["data"]) == 2 and r1.json()["page"]["has_more"]
    rest = ws.api.get(
        "/matches",
        params={"remittance_id": doc["id"], "limit": 50, "cursor": r1.json()["page"]["next_cursor"]},
    )
    ids = [m["id"] for m in r1.json()["data"] + rest.json()["data"]]
    assert len(ids) == len(set(ids)) == 5  # lines 1-5 have a match; line 6 none
    only = ws.api.get("/matches", params={"remittance_id": doc["id"], "status": "suggested"})
    assert only.json()["data"] == []
    assert ws.api.get("/matches", params={"bogus": 1}).status_code == 400


# ---- finalisation ----------------------------------------------------------------------------------------------


def test_finalising_classifies_the_variances_of_the_ground_truth(story: Story) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    seen: list[FinalisedRemittance] = []

    def hook(_db: Any, _who: Who, result: FinalisedRemittance) -> None:
        seen.append(result)

    reconciliation.on_finalise(hook)
    try:
        k = key()
        r = ws.api.post(f"/remittances/{doc['id']}/finalize", headers=k)
        assert r.status_code == 200, r.text
        replay = ws.api.post(f"/remittances/{doc['id']}/finalize", headers=k)
        assert replay.status_code == 200 and replay.json() == r.json()
        assert len(seen) == 1  # the replay did not finalise twice
    finally:
        matching._HOOKS.remove(hook)
    body = r.json()
    assert body["remittance"]["status"] == "reconciled"
    check_findings(story, body)
    assert [f.case_type for f in seen[0].findings].count("unpaid") == 1
    assert seen[0].document_number == "CM-7781" and str(seen[0].payment_date) == "2026-07-31"


def test_finalising_opened_the_recovery_cases_of_the_ground_truth(story: Story) -> None:
    """S9 hooks into the finalisation: expected_reconciliation.json gives 150.00 short-paid (E12), 2,080.00 rejected
    (E07), 75.00 unmatched and 660.00 unpaid; the dispute deadline is 2026-07-31 + 60 days = 2026-09-29; the unpaid
    line has no remittance, so it counts from the claim submission: 2026-07-05 + 60 = 2026-09-03 (D-N2)."""
    got = story.ws.api.get("/cases", params={"limit": 50}).json()["data"]
    assert sorted((c["case_type"], c["variance_amount"], c["deadline_date"]) for c in got) == [
        ("rejected", "2080.0000", "2026-09-29"),
        ("short_paid", "150.0000", "2026-09-29"),
        ("unmatched_payment", "75.0000", "2026-09-29"),
        ("unpaid", "660.0000", "2026-09-03"),
    ]
    assert {c["remittance_document_id"] for c in got} == {SHARED["doc"]["id"]}


def test_a_finalised_remittance_is_final(story: Story, admin_engine: Engine) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    again = finalize(ws.api, doc["id"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
    assert ws.api.post(f"/remittances/{doc['id']}/reconcile").status_code == 409
    assert ws.api.post(f"/remittances/{doc['id']}/void").status_code == 409
    with admin_engine.begin() as conn:
        for sql in (
            "UPDATE remittance_documents SET status = 'accepted' WHERE id = :d",
            "UPDATE remittance_documents SET total_amount = 1 WHERE id = :d",
            "DELETE FROM reconciliation_matches WHERE remittance_line_id IN "
            "(SELECT id FROM remittance_lines WHERE remittance_document_id = :d)",
        ):
            sp = conn.begin_nested()
            try:
                conn.execute(text(sql), {"d": doc["id"]})
            except Exception as exc:
                sp.rollback()
                assert "never changes" in str(exc) or "never change" in str(exc), exc
            else:
                sp.rollback()
                raise AssertionError(f"owner could change a finalised remittance: {sql}")
    drain(ws.app)
