"""S8 large remittance: the ground-truth CM-7781 lines plus 4,994 payment lines of 10.00 that no claim explains (5,000
lines, hand total 7545.00 + 4994 * 10.00 = 57,485.00) are ingested and reconciled through the real job queue. The
timings are printed (``pytest -s``) and recorded in archived note i1-s8-reconciliation.md (not in repo); the bound only guards
against a regression to quadratic behaviour. The claim side is the 8 ground-truth claim lines (a limitation, noted)."""

import time
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal

from tests.conftest import DbUrls
from tests.reconciliation.support import CSV, build_story, ingest, listing_of, reconcile, upload
from tests.support import fake_clock, make_app
from tests.testdata import data

CM_CSV_LINES = data("remittance/credit_memo_VOLT_CM-7781.csv").decode()

NOISE = 4994
BOUND_SECONDS = 300


def test_a_5000_line_remittance_is_ingested_and_reconciled_in_bounded_time(migrated_db: DbUrls) -> None:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
        story = build_story(app, stack)
        ws, volt = story.ws, story.mfr["VOLT"]
        noise = "".join(f"{7 + i},,,,,,10.00,,\n" for i in range(NOISE))
        body = (CM_CSV_LINES + noise).encode()
        t0 = time.perf_counter()
        doc = ingest(
            ws, upload(ws, "big.csv", body, CSV), volt, document_number="BIG-1", total_amount="57485.00"
        )
        t1 = time.perf_counter()
        assert doc["status"] == "accepted" and Decimal(doc["total_amount"]) == Decimal("57485.00")
        reconcile(ws, doc["id"])
        t2 = time.perf_counter()
        rows = {
            x["line"]["line_number"]: x for x in listing_of(ws, f"/remittances/{doc['id']}/reconciliation")
        }
        print(f"\nPERF lines=5000 ingest={t1 - t0:.1f}s reconcile={t2 - t1:.1f}s")
        assert len(rows) == 5000
        assert [rows[n]["state"] for n in (1, 2, 3, 4)] == ["matched"] * 4  # as in the ground truth
        assert rows[5]["state"] == "suggested"  # GL-V2 lump
        assert all(rows[n]["state"] == "unmatched" for n in range(6, 5001))
        assert t2 - t0 < BOUND_SECONDS
