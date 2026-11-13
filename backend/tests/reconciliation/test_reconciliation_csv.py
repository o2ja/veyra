"""S8 remittance CM-7781 (CSV) end to end on real Postgres: ingest -> matching -> decisions -> finalisation, against
``testdata/generated/ground_truth/expected_reconciliation.json``. Money in the expectations is the ground truth or a
hand derivation, never the output of the code under test. Tests of the scenario share a module-scoped workspace and
run in file order."""

import uuid
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
    categorise_unmatched,
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


# ---- categorisation (BL §10.3: all unmatched lines explicitly categorised) ---------------------------------------


def test_finalising_needs_every_unmatched_line_categorised(story: Story, admin_engine: Engine) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    rows = view(ws, doc["id"])
    refused = finalize(ws.api, doc["id"])  # line 6 (75.00) is neither matched nor categorised
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "uncategorised_lines"
    matched, open_ = rows[4]["line"]["id"], rows[6]["line"]["id"]
    body = {"category": "unmatched_payment"}
    held = ws.api.post(f"/remittance-lines/{matched}/categorise", json=body)
    assert held.status_code == 409 and held.json()["error"]["code"] == "line_matched"
    assert ws.api.post(f"/remittance-lines/{open_}/categorise", json={"category": "other"}).status_code == 422
    assert ws.api.post(f"/remittance-lines/{uuid.uuid4()}/categorise", json=body).status_code == 404
    done = ws.api.post(f"/remittance-lines/{open_}/categorise", json=body)
    assert done.status_code == 200 and done.json()["category"] == "unmatched_payment"
    again = ws.api.post(f"/remittance-lines/{open_}/categorise", json=body)
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
    log = ws.api.get("/audit-log", params={"action": "remittance_line.categorised"})
    assert log.status_code == 200 and [e["entity_id"] for e in log.json()["data"]] == [open_]
    with (
        admin_engine.begin() as conn
    ):  # the line stays evidence: nothing but a first categorisation is possible
        for sql, target in (
            ("UPDATE remittance_lines SET amount_paid = 1 WHERE id = :i", open_),
            (
                "UPDATE remittance_lines SET category = 'unmatched_payment', categorised_by = :u, "
                "categorised_at = now() WHERE id = :i",
                matched,
            ),
        ):
            sp = conn.begin_nested()
            try:
                conn.execute(text(sql), {"i": target, **({"u": ws.user_id} if ":u" in sql else {})})
            except Exception as exc:
                sp.rollback()
                assert "categoris" in str(exc), exc
            else:
                sp.rollback()
                raise AssertionError(f"line changed: {sql}")


def test_a_categorised_line_cannot_be_matched_any_more(story: Story) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    line6 = view(ws, doc["id"])[6]["line"]["id"]
    # categorise here (the earlier finalisation test may have done it already: then 409 invalid_state)
    done = ws.api.post(f"/remittance-lines/{line6}/categorise", json={"category": "unmatched_payment"})
    assert done.status_code in (200, 409), done.text
    r = ws.api.post(
        "/matches",
        json={
            "remittance_line_id": line6,
            "claim_line_id": story.lines["INV-10001"]["id"],
            "allocated_amount": "1.00",
        },
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "line_categorised"


def test_an_adjustment_allocation_on_a_paid_line_is_not_cash_on_a_claim_and_adds_no_remainder(
    story: Story,
) -> None:
    """Line 4 (200.00) is allocated 200.00 to CL-4; a person also confirms -10.00 of the -50.00 adjustment item on it. Claim parts
    = 200.00, remainder = 200.00 - 200.00 = 0 (no extra unmatched_payment); CL-4 stays claimed 250.00 - paid 200.00 =
    short_paid 50.00, asserted by the finalisation test below (it expects exactly one CL-4 finding and no more)."""
    ws = story.ws
    line4 = view(ws, SHARED["doc"]["id"])[4]["line"]["id"]
    r = ws.api.post(
        "/matches",
        json={
            "remittance_line_id": line4,
            "claim_adjustment_id": story.adjustment["id"],
            "allocated_amount": "-10.00",  # the ADJ is spent across tests: -10.00 here, -20.00 next, -20.00 after
        },
    )
    assert r.status_code == 201, r.text


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


# ---- the owner's money rule and the review fixes (hand derivations in the comments) ----------------------------

HEADER = "line,invoice_no,invoice_line,part_no,qty,amount_claimed,amount_paid,reason_code,reason_text\n"


def test_no_stated_total_is_not_auto_accepted(story: Story) -> None:
    """A file without any total makes the arithmetic check empty: a person enters it (422 total_required)."""
    ws, volt = story.ws, story.mfr["VOLT"]
    body = (HEADER + "1,,,,,,5.00,,x\n").encode()
    doc = ingest(ws, upload(ws, "nt.csv", body, CSV), volt, document_number="NT-1")
    assert doc["status"] == "pending_review"
    ex = ws.api.get(f"/remittances/{doc['id']}/extraction").json()
    assert [p["code"] for p in ex["problems"]] == ["total_required"]
    refused = ws.api.post(f"/remittances/{doc['id']}/accept", json={})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "total_required"
    ok_ = ws.api.post(
        f"/remittances/{doc['id']}/accept", json={"total_amount": "5.00"}
    )  # hand: one line, 5.00
    assert ok_.status_code == 200 and ok_.json()["status"] == "accepted"


def test_a_zero_paid_line_makes_its_claim_line_rejected_via_a_zero_manual_match(story: Story) -> None:
    """CL-7 (INV-10060, 660.00) was reported unpaid by CM-7781. A new remittance pays it 0.00 with E07 (mapped to
    rejected_quantity_exceeds_authorization) on a line no tier can match: a 0.00 manual match makes it rejected 660.00;
    the 0.00 line itself yields no finding. The 5.00 line allows no 0.00 match and, categorised, is an unmatched
    payment of 5.00."""
    ws, volt = story.ws, story.mfr["VOLT"]
    body = (HEADER + "1,,,,,,0.00,E07,Exceeds authorized quantity\n2,,,,,,5.00,,Credit\n").encode()
    doc = ingest(ws, upload(ws, "zero.csv", body, CSV), volt, document_number="ZERO-1", total_amount="5.00")
    assert doc["status"] == "accepted"
    reconcile(ws, doc["id"])
    rows = view(ws, doc["id"])
    assert rows[1]["state"] == "unmatched" and rows[2]["state"] == "unmatched"
    cl7 = story.lines["INV-10060"]["id"]
    bad = ws.api.post(
        "/matches",
        json={"remittance_line_id": rows[2]["line"]["id"], "claim_line_id": cl7, "allocated_amount": "0.00"},
    )
    assert bad.status_code == 422
    ok_ = ws.api.post(
        "/matches",
        json={"remittance_line_id": rows[1]["line"]["id"], "claim_line_id": cl7, "allocated_amount": "0.00"},
    )
    assert ok_.status_code == 201, ok_.text
    assert categorise_unmatched(ws, doc["id"]) == [2]
    r = finalize(ws.api, doc["id"])
    assert r.status_code == 200, r.text
    got = [(f["case_type"], f["reason_code"], Decimal(f["amount"])) for f in r.json()["findings"]]
    # the 0.00 line yields nothing; the categorised 5.00 line is an unmatched payment of 5.00
    assert sorted(got) == [
        ("rejected", "rejected_quantity_exceeds_authorization", Decimal("660.00")),
        ("unmatched_payment", "unmatched_payment", Decimal("5.00")),
    ]


def test_an_adjustment_item_is_matched_only_by_a_person_and_yields_no_finding(story: Story) -> None:
    """Interim rule pending owner decision D-N11. ADJ-2026-0001 (-50.00) rides in batch B2, not in CL-4's batch: it is a
    separate item. Only a manual match with its id gives it an allocation; its original line's paid is untouched."""
    ws, volt = story.ws, story.mfr["VOLT"]
    adj = story.adjustment["id"]
    body = (HEADER + "1,,,,,,100.00,,x\n").encode()
    doc = ingest(ws, upload(ws, "adj.csv", body, CSV), volt, document_number="ADJ-1", total_amount="100.00")
    reconcile(ws, doc["id"])
    line = view(ws, doc["id"])[1]
    assert line["state"] == "unmatched"  # no tier ever allocates to an adjustment on its own
    lid = line["line"]["id"]
    both = {
        "remittance_line_id": lid,
        "claim_line_id": story.lines["INV-10021"]["id"],
        "claim_adjustment_id": adj,
    }
    assert ws.api.post("/matches", json={**both, "allocated_amount": "-50.00"}).status_code == 422
    one = {"remittance_line_id": lid, "claim_adjustment_id": adj}
    for wrong in ("0.00", "50.00", "-60.00"):  # zero, the wrong sign, larger than the adjustment (-50.00)
        assert ws.api.post("/matches", json={**one, "allocated_amount": wrong}).status_code == 422, wrong
    ok_ = ws.api.post("/matches", json={**one, "allocated_amount": "-20.00"})  # -10.00 is on line 4 already
    assert ok_.status_code == 201, ok_.text
    (a,) = ok_.json()["allocations"]
    assert (a["claim_line_id"], a["claim_adjustment_id"], Decimal(a["allocated_amount"])) == (
        None,
        adj,
        Decimal("-20.00"),
    )
    assert view(ws, doc["id"])[1]["state"] == "matched"
    # Hand: the line paid 100.00 and only an adjustment item is confirmed on it. Under the D-N11 interim that
    # allocation is not cash on a claim line, so claim parts = 0 and the whole line is the remainder: one
    # unmatched_payment of 100.00 (= amount_paid, never more, never 120.00 = 100.00 - (-20.00)). No categorisation is
    # needed (the line has a confirmed match) and the adjustment item itself yields no finding.
    final = finalize(ws.api, doc["id"])
    assert final.status_code == 200, final.text
    # only this line's findings: the story's other claim lines may be reported unpaid (not reached by this document)
    got = [
        (f["case_type"], Decimal(f["amount"]))
        for f in final.json()["findings"]
        if f["remittance_line_id"] == lid
    ]
    assert got == [("unmatched_payment", Decimal("100.00"))]


def _raw_confirmed(
    conn: Any, ws: Any, line_id: str, claim_line_id: str | None, amount: str, adjustment_id: str | None = None
) -> None:
    """Insert a confirmed allocation straight into the table (the database invariants, without the service)."""
    tenant = conn.execute(
        text("SELECT tenant_id FROM remittance_lines WHERE id = :l"), {"l": line_id}
    ).scalar_one()
    mid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO reconciliation_matches (tenant_id, id, match_id, remittance_line_id, claim_line_id, "
            "allocated_amount, method, evidence, status, confirmed_by, confirmed_at, claim_adjustment_id) VALUES "
            "(:t, :m, :m, :l, :c, :a, 'manual', '{}', 'confirmed', :u, now(), :j)"
        ),
        {
            "t": tenant,
            "m": mid,
            "l": line_id,
            "c": claim_line_id,
            "a": amount,
            "u": ws.user_id,
            "j": adjustment_id,
        },
    )


def test_cross_batch_adjustment_allocation_never_frees_room_for_claim_lines_and_categorised_lines_stay_closed(
    story: Story, admin_engine: Engine, app_engine: Engine
) -> None:
    """Line 1 pays 200.00. A person gives 200.00 to INV-10010 and confirms -20.00 of the cross-batch ADJ on it. A further
    20.00 to INV-10011 would make 200.00 + 20.00 = 220.00 > 200.00 (the -20.00 counts 0): refused (409 match_invariant),
    although the signed sum 200.00 - 20.00 + 20.00 = 200.00 would fit. The database trigger refuses the same insert.
    The ADJ (-50.00) can be allocated up to its amount over all lines and no further (422; trigger as rebate_app).
    Line 3 (300.00, within 5% of the 312.50 carrier claim by invoice + SKU + quantity) is only suggested (T3); once a
    person categorises it, confirming the suggestion is refused (409 line_categorised), in the service and in the
    database, and rejecting still works."""
    ws, volt = story.ws, story.mfr["VOLT"]
    carrier = story.lines["INV-10020"]
    body = HEADER + (
        "1,,,,,,200.00,,pays two claims\n2,,,,,,5.00,,x\n"
        f"3,INV-10020,,BRK-3P-60A,{carrier['quantity']},,300.00,,near\n"
    )
    doc = ingest(
        ws,
        upload(ws, "guard.csv", body.encode(), CSV),
        volt,
        document_number="GUARD-1",
        total_amount="505.00",
    )
    assert doc["status"] == "accepted"
    reconcile(ws, doc["id"])
    rows = view(ws, doc["id"])
    assert rows[1]["state"] == "unmatched" and rows[3]["state"] == "suggested"
    line1, line2, line3 = (rows[n]["line"]["id"] for n in (1, 2, 3))
    a, b = story.lines["INV-10010"]["id"], story.lines["INV-10011"]["id"]

    def post(**kw: str) -> Any:
        return ws.api.post("/matches", json={"remittance_line_id": line1, **kw})

    assert post(claim_line_id=a, allocated_amount="200.00").status_code == 201
    assert post(claim_adjustment_id=story.adjustment["id"], allocated_amount="-20.00").status_code == 201
    refused = post(claim_line_id=b, allocated_amount="20.00")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "match_invariant"
    # the ADJ's cap: allocate exactly the room left (whatever earlier tests spent), then one cent more is refused
    adj = story.adjustment["id"]
    with admin_engine.connect() as conn:
        spent = conn.execute(
            text(
                "SELECT COALESCE(sum(allocated_amount), 0) FROM reconciliation_matches "
                "WHERE claim_adjustment_id = :i AND status = 'confirmed'"
            ),
            {"i": adj},
        ).scalar_one()
    room = Decimal("-50.00") - spent  # <= 0
    on_line1 = {"remittance_line_id": line1, "claim_adjustment_id": adj}
    if room != 0:
        assert ws.api.post("/matches", json={**on_line1, "allocated_amount": str(room)}).status_code == 201
    over = ws.api.post("/matches", json={**on_line1, "allocated_amount": "-0.01"})
    assert over.status_code == 422, over.text
    with app_engine.connect() as conn:  # the database refuses the same over-allocation (as rebate_app)
        tx = conn.begin()
        try:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ws.tenant_id})
            _raw_confirmed(conn, ws, line1, None, "-0.01", adj)
            with pytest.raises(Exception, match="exceed the adjustment amount"):
                conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            tx.rollback()
    with admin_engine.begin() as conn:
        _raw_confirmed(conn, ws, line1, a, "0.00")  # no effect on the sum
        sp = conn.begin_nested()
        _raw_confirmed(conn, ws, line1, b, "20.00")
        with pytest.raises(Exception, match="exceed the amount paid"):
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        sp.rollback()
    # categorisation is final: the suggestion on line 3 cannot be confirmed any more
    (m,) = rows[3]["matches"]
    assert m["status"] == "suggested"
    assert (
        ws.api.post(
            f"/remittance-lines/{line3}/categorise", json={"category": "unmatched_payment"}
        ).status_code
        == 200
    )
    assert (
        ws.api.post(
            f"/remittance-lines/{line2}/categorise", json={"category": "unmatched_payment"}
        ).status_code
        == 200
    )
    held = ws.api.post(f"/matches/{m['id']}/confirm", headers={"If-Match": f'"{m["revision"]}"'})
    assert held.status_code == 409 and held.json()["error"]["code"] == "line_categorised"
    assert ws.api.get(f"/matches/{m['id']}").json()["status"] == "suggested"
    with admin_engine.begin() as conn:
        for sql in (
            "UPDATE reconciliation_matches SET status = 'confirmed', revision = revision + 1, confirmed_by = :u, "
            "confirmed_at = now() WHERE match_id = :m",
            None,
        ):
            sp = conn.begin_nested()
            with pytest.raises(Exception, match="categorised line"):
                if sql:
                    conn.execute(text(sql), {"u": ws.user_id, "m": m["id"]})
                else:
                    _raw_confirmed(conn, ws, line2, a, "1.00")
            sp.rollback()
    rejected = ws.api.post(f"/matches/{m['id']}/reject", headers={"If-Match": f'"{m["revision"]}"'})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"


def test_a_positive_cross_batch_adjustment_allocation_consumes_room_and_a_same_batch_one_is_face_value(
    story: Story, admin_engine: Engine
) -> None:
    """Hand derivation, all in ONE admin transaction that is always rolled back (nothing is committed; the S7 flow of this
    module has no positive adjustment, so +25.00 is set on the fixture row inside the transaction, with triggers off for
    that statement only). A line pays 225.00. (a) 200.00 -> CL-4 and +25.00 -> the cross-batch ADJ: 225.00 <= 225.00,
    accepted. (b) 225.00 -> CL-4 and +25.00 -> ADJ: 250.00 > 225.00, refused, as rebate_app. Then a same-batch
    adjustment allocation must equal the adjustment's amount (-50.00 passes, -1000.00 is refused)."""
    ws, volt = story.ws, story.mfr["VOLT"]
    adj, cl4 = story.adjustment["id"], story.lines["INV-10021"]["id"]
    body = HEADER + "1,,,,,,225.00,,pays CL-4\n"
    doc = ingest(
        ws, upload(ws, "plus.csv", body.encode(), CSV), volt, document_number="PLUS-1", total_amount="225.00"
    )
    line = view(ws, doc["id"])[1]["line"]["id"]

    def check(conn: Any) -> None:
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))

    with admin_engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(text("UPDATE claim_adjustments SET amount = 25.00 WHERE id = :i"), {"i": adj})
            # other tests' allocations on the ADJ are irrelevant to this case
            conn.execute(
                text("DELETE FROM reconciliation_matches WHERE claim_adjustment_id = :i"), {"i": adj}
            )
            conn.execute(text("SET LOCAL session_replication_role = origin"))
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ws.tenant_id})
            conn.execute(text("SET LOCAL ROLE rebate_app"))
            sp = conn.begin_nested()
            _raw_confirmed(conn, ws, line, cl4, "200.00")
            _raw_confirmed(conn, ws, line, None, "25.00", adj)
            check(conn)  # (a) 225.00 <= 225.00
            sp.rollback()
            sp = conn.begin_nested()
            _raw_confirmed(conn, ws, line, cl4, "225.00")
            _raw_confirmed(conn, ws, line, None, "25.00", adj)
            with pytest.raises(Exception, match="exceed the amount paid"):
                check(conn)  # (b) 250.00 > 225.00
            sp.rollback()
            conn.execute(text("RESET ROLE"))
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(text("UPDATE claim_adjustments SET amount = -50.00 WHERE id = :i"), {"i": adj})
            conn.execute(
                text(
                    "UPDATE claim_adjustments SET included_in_batch_id = "
                    "(SELECT batch_id FROM claim_lines WHERE id = :c) WHERE id = :i"
                ),
                {"c": cl4, "i": adj},
            )
            conn.execute(text("SET LOCAL session_replication_role = origin"))
            sp = conn.begin_nested()
            _raw_confirmed(conn, ws, line, None, "-50.00", adj)
            check(conn)  # face value: accepted
            sp.rollback()
            sp = conn.begin_nested()
            _raw_confirmed(conn, ws, line, None, "-1000.00", adj)
            with pytest.raises(Exception, match="same-batch adjustment allocation"):
                check(conn)
            sp.rollback()
        finally:
            tx.rollback()
