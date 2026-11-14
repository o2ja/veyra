"""S9 reclaim recovery through the real flow only (no direct inserts): CM-7781 finalised -> 2,080.00 rejected case ->
reclaim -> the proposed adjustment approved and carried by a submitted batch -> a remittance that pays it, matched by a
person through POST /matches (S8's guards apply) -> finalised -> the case is recovered."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI

from tests.conftest import DbUrls
from tests.integration.recovery_support import act
from tests.reconciliation.support import (
    CSV,
    EXPECTED,
    Story,
    build_story,
    categorise_unmatched,
    finalize,
    ingest,
    key,
    pool,
    reconcile,
    upload,
    view,
)
from tests.support import FakeClock, drain, fake_clock, make_app
from tests.testdata import data

D = Decimal
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
HEADER = "line,invoice_no,invoice_line,part_no,qty,amount_claimed,amount_paid,reason_code,reason_text\n"


@pytest.fixture(scope="module")
def story(migrated_db: DbUrls) -> Iterator[Story]:
    app: FastAPI = make_app(migrated_db.app)
    fc: FakeClock
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield build_story(app, stack)


def test_a_reclaim_paid_by_a_remittance_recovers_the_case(story: Story) -> None:
    """Hand derivation: E07 line 3 of CM-7781 is claimed 2,080.00 and paid 0.00 -> rejected case 2,080.00. The reclaim
    adjustment is +2,080.00 (positive, recovery_reclaim). A remittance paying 2,080.00 matched to it in full:
    recovered = 2,080.00 = variance 2,080.00 - 0.00 -> status recovered, closed. The carrier batch total is the
    carrier line + 2,080.00; the payment is cash on the adjustment only (no claim line is paid by it)."""
    ws, volt = story.ws, story.mfr["VOLT"]
    # CM-7781, the ground-truth credit memo, finalised as in the S8 flow
    cm = ingest(
        ws,
        upload(ws, "credit_memo_VOLT_CM-7781.csv", data("remittance/credit_memo_VOLT_CM-7781.csv"), CSV),
        volt,
        document_number="CM-7781",
        document_date="2026-07-31",
        payment_date="2026-07-31",
        total_amount=EXPECTED["document_total"],
    )
    reconcile(ws, cm["id"])
    (match,) = [
        m for r in view(ws, cm["id"]).values() for m in r["matches"] if m["status"] == "suggested"
    ]  # line 5, the 2,100.00 subset
    assert (
        ws.api.post(
            f"/matches/{match['id']}/confirm", headers={"If-Match": f'"{match["revision"]}"'}
        ).status_code
        == 200
    )
    categorise_unmatched(ws, cm["id"])
    assert finalize(ws.api, cm["id"]).status_code == 200
    cases = ws.api.get("/cases", params={"limit": 50}).json()["data"]
    (rejected,) = [c for c in cases if c["case_type"] == "rejected"]
    assert D(rejected["variance_amount"]) == D("2080.00") and rejected["status"] == "open"

    # reclaim -> approve the proposed adjustment -> carried by a submitted batch (with a still-unclaimed line)
    r = act(ws.api, "post", f"/cases/{rejected['id']}/reclaim", rejected["id"])
    assert r.status_code == 200, r.text
    adj = r.json()["adjustment_id"]
    assert ws.api.post(f"/claim-adjustments/{adj}/approve").status_code == 200
    carrier = next(x for x in pool(ws.api).values() if x["manufacturer_id"] == volt)
    b = ws.api.post(
        "/claim-batches",
        json={"manufacturer_id": volt, "result_ids": [carrier["id"]], "include_adjustment_ids": [adj]},
        headers=key(),
    )
    assert b.status_code == 201, b.text
    assert ws.api.post(f"/claim-batches/{b.json()['id']}/approve").status_code == 200
    s = ws.api.post(
        f"/claim-batches/{b.json()['id']}/submit",
        json={"submitted_at": "2026-07-09", "external_reference": "VOLT-RECLAIM-1"},
        headers=key(),
    )
    assert s.status_code == 200, s.text

    # the manufacturer pays 2,080.00; a person matches the line to the adjustment (S8: only a person may)
    body = (HEADER + "1,,,,,,2080.00,,Reclaim of E07\n").encode()
    pay = ingest(
        ws,
        upload(ws, "reclaim_payment.csv", body, CSV),
        volt,
        document_number=f"RC-{uuid.uuid4().hex[:6]}",
        document_date="2026-07-10",
        payment_date="2026-07-10",
        total_amount="2080.00",
    )
    reconcile(ws, pay["id"])
    line = view(ws, pay["id"])[1]["line"]["id"]
    m = ws.api.post(
        "/matches",
        json={"remittance_line_id": line, "claim_adjustment_id": adj, "allocated_amount": "2080.00"},
    )
    assert m.status_code == 201, m.text
    done = finalize(ws.api, pay["id"])
    assert done.status_code == 200, done.text
    drain(ws.app)
    got = ws.api.get(f"/cases/{rejected['id']}").json()
    assert (got["status"], D(got["recovered_amount"])) == ("recovered", D("2080.00")) and got["closed_at"]
