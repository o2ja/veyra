"""Owner decision D-N11 (2026-11-14): a later payment's negative adjustment line is allocated to its OWN claim line
(CL-4, INV-10021/1), never to the invoice line of the batch that carries it (INV-10020/1). The story is the ground
truth's: B1 claims CL-4 250.00 (frozen); ADJ-2026-0001 (return reversal -50.00, credit note CN-5002) rides in B2
together with INV-10020/1 (312.50), so B2 = 312.50 - 50.00 = 262.50. Every number is hand-derived in the comments, never
read from the code under test. Each test builds its own story (the scenarios must not share payments)."""

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests.conftest import DbUrls
from tests.reconciliation.support import CSV, Story, build_story, finalize, ingest, reconcile, upload, view
from tests.support import FakeClock, drain, fake_clock, make_app

D = Decimal
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
HEADER = "line,invoice_no,invoice_line,part_no,qty,amount_claimed,amount_paid,reason_code,reason_text\n"
# B1 = 2200 + 3120 + 2080 + 250 + 1000 + 1100 + 660 = 10,410.00; without CL-4's 250.00 the other six lines are 10,160.00
B1_OTHER_LINES = D("10160.00")


class Env:
    def __init__(self, app: FastAPI, stack: ExitStack, clock: FakeClock) -> None:
        self.app, self.stack, self.clock = app, stack, clock


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


@pytest.fixture
def story(env: Env) -> Story:
    return build_story(env.app, env.stack)


def _remit(story: Story, number: str, rows: str, total: str) -> dict[str, Any]:
    ws = story.ws
    doc = ingest(
        ws,
        upload(ws, f"{number}.csv", (HEADER + rows).encode(), CSV),
        story.mfr["VOLT"],
        document_number=number,
        document_date="2026-07-10",
        payment_date="2026-07-10",
        total_amount=total,
    )
    assert doc["status"] == "accepted", doc
    reconcile(ws, doc["id"])
    return doc


def _b1_pays_cl4(story: Story) -> None:
    """B1 pays CL-4 200.00 of 250.00 claimed: CL-4 short_paid 250.00 - 200.00 = 50.00 (the credit is not applied)."""
    doc = _remit(story, "B1-PAY", "1,INV-10021,1,BRK-3P-60A,16,250.00,200.00,,\n", "200.00")
    assert finalize(story.ws.api, doc["id"]).status_code == 200


def _b2_rows(story: Story, *amounts: str) -> str:
    qty = story.lines["INV-10020"]["quantity"]
    return "".join(f"{i},INV-10020,1,BRK-3P-60A,{qty},312.50,{a},,\n" for i, a in enumerate(amounts, 1))


def _cases(story: Story) -> dict[str, list[Decimal]]:
    """invoice number -> variance amounts of its cases (story claim lines only)."""
    by_id = {v["id"]: inv for inv, v in story.lines.items()}
    out: dict[str, list[Decimal]] = {}
    for c in story.ws.api.get("/cases", params={"limit": 200}).json()["data"]:
        if c["claim_line_id"] in by_id:
            out.setdefault(by_id[c["claim_line_id"]], []).append(D(c["variance_amount"]))
    return out


def _alloc_rows(admin: Engine, remittance_id: str) -> list[tuple[int, str | None, str | None, Decimal]]:
    """(remittance line number, claim line id, the adjusted claim line of the adjustment item, amount) per confirmed row."""
    with admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT l.line_number, m.claim_line_id::text, a.claim_line_id::text, m.allocated_amount "
                "FROM reconciliation_matches m JOIN remittance_lines l ON l.id = m.remittance_line_id "
                "LEFT JOIN claim_adjustments a ON a.id = m.claim_adjustment_id "
                "WHERE l.remittance_document_id = :d AND m.status = 'confirmed' "
                "ORDER BY l.line_number, m.allocated_amount DESC"
            ),
            {"d": remittance_id},
        )
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def test_inv_10020_shows_no_short_pay_when_b2_pays_262_50_and_cl4_is_counted_once(
    env: Env, story: Story, admin_engine: Engine
) -> None:
    """B1 paid 200.00 on CL-4 (250.00): CL-4 short 250.00 - 200.00 = 50.00. B2 = 312.50 + (-50.00) = 262.50 paid on ONE
    line against INV-10020. Split (like a same-batch adjustment): the adjustment at face value -50.00, the rest to the
    claim line: 262.50 - (-50.00) = 312.50. INV-10020: claimed 312.50, paid 312.50 -> no case. The -50.00 belongs to
    ADJ-2026-0001 whose own claim line is CL-4 (never INV-10020). Line sum 312.50 + (-50.00) = 262.50 = amount_paid.
    CL-4 keeps its single 50.00 case. S10: claimed = B1 10,410.00 + B2 262.50 = 10,672.50; collected = 200.00 (CL-4) +
    312.50 - 50.00 = 462.50; short-paid = CL-4 50.00 + the six other B1 lines unpaid (overdue: submitted 07-05, lag 3
    days) 10,160.00 = 10,210.00 = claimed - collected (10,672.50 - 462.50)."""
    ws = story.ws
    _b1_pays_cl4(story)
    doc = _remit(story, "B2-PAY", _b2_rows(story, "262.50"), "262.50")
    assert view(ws, doc["id"])[1]["state"] == "matched"
    inv20, cl4 = story.lines["INV-10020"]["id"], story.lines["INV-10021"]["id"]
    assert _alloc_rows(admin_engine, doc["id"]) == [
        (1, inv20, None, D("312.50")),
        (1, None, cl4, D("-50.00")),  # attributed to CL-4 through its adjustment item
    ]
    assert D("312.50") + D("-50.00") == D("262.50")  # sum of allocations = amount_paid
    assert finalize(ws.api, doc["id"]).status_code == 200
    cases = _cases(story)
    assert (
        "INV-10020" not in cases
    )  # no short-pay from an adjustment already carried and resolved in its own batch
    assert cases["INV-10021"] == [D("50.00")]  # CL-4 counted once
    drain(env.app)  # the finalisations queued refreshes (deduped per minute on the fixed test clock)
    env.clock.t = NOW + timedelta(minutes=1)
    assert ws.api.post("/dashboard/refresh").status_code == 202
    drain(env.app)
    f = {k: D(v) for k, v in ws.api.get("/dashboard").json()["figures"].items()}
    assert (f["claimed"], f["collected"], f["short_paid"]) == (D("10672.50"), D("462.50"), D("10210.00"))
    assert f["claimed"] - f["collected"] == f["short_paid"] == D("50.00") + B1_OTHER_LINES


def test_b2_paid_in_two_instalments_allocates_the_adjustment_once(story: Story, admin_engine: Engine) -> None:
    """B2 paid 100.00 then 162.50 (hand total 262.50). First line: adjustment -50.00, claim part 100.00 + 50.00 =
    150.00 (sum 100.00). The second is a top-up of the same claim line (T2): all 162.50 to INV-10020, the adjustment is
    not split again. INV-10020 paid 150.00 + 162.50 = 312.50 = claimed -> no case; the adjustment totals -50.00 once."""
    ws = story.ws
    _b1_pays_cl4(story)
    first = _remit(story, "B2-I1", _b2_rows(story, "100.00"), "100.00")
    second = _remit(story, "B2-I2", _b2_rows(story, "162.50"), "162.50")
    inv20, cl4 = story.lines["INV-10020"]["id"], story.lines["INV-10021"]["id"]
    assert _alloc_rows(admin_engine, first["id"]) == [
        (1, inv20, None, D("150.00")),
        (1, None, cl4, D("-50.00")),
    ]
    assert _alloc_rows(admin_engine, second["id"]) == [(1, inv20, None, D("162.50"))]
    assert finalize(ws.api, second["id"]).status_code == 200
    cases = _cases(story)
    assert "INV-10020" not in cases and cases["INV-10021"] == [D("50.00")]


def test_b2_paid_short_leaves_exactly_the_shortfall_on_inv_10020(story: Story, admin_engine: Engine) -> None:
    """B2 expected 262.50, paid 250.00 (short 12.50). Split: adjustment -50.00, claim part 250.00 + 50.00 = 300.00.
    INV-10020: claimed 312.50 - paid 300.00 = 12.50 short (not 62.50 = 312.50 - 250.00). Line sum 300.00 - 50.00 =
    250.00. CL-4 still has its one 50.00 case."""
    ws = story.ws
    _b1_pays_cl4(story)
    doc = _remit(story, "B2-SHORT", _b2_rows(story, "250.00"), "250.00")
    inv20, cl4 = story.lines["INV-10020"]["id"], story.lines["INV-10021"]["id"]
    assert _alloc_rows(admin_engine, doc["id"]) == [
        (1, inv20, None, D("300.00")),
        (1, None, cl4, D("-50.00")),
    ]
    assert finalize(ws.api, doc["id"]).status_code == 200
    cases = _cases(story)
    assert cases["INV-10020"] == [D("12.50")] and cases["INV-10021"] == [D("50.00")]


def test_a_carried_adjustment_whose_batch_is_not_paid_changes_nothing(
    story: Story, admin_engine: Engine
) -> None:
    """Only B1 is paid (CL-4 200.00): no allocation touches ADJ-2026-0001 (B2 is unpaid; submitted 07-09, lag 3 days,
    so no unpaid case on INV-10020 either); CL-4 short 50.00."""
    _b1_pays_cl4(story)
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM reconciliation_matches WHERE claim_adjustment_id = :a"),
            {"a": story.adjustment["id"]},
        ).scalar_one()
    assert n == 0
    cases = _cases(story)
    assert "INV-10020" not in cases and cases["INV-10021"] == [D("50.00")]


def test_a_positive_carried_adjustment_is_not_split_off_the_payment(
    story: Story, admin_engine: Engine
) -> None:
    """Only a NEGATIVE adjustment is split (a credit netted inside the payment). With the ADJ forced to +25.00 (fixture
    edit, triggers off for that statement), B2 paid 312.50 on one line: all 312.50 goes to INV-10020 (claimed 312.50, no
    case) and the adjustment item gets no allocation (unchanged by D-N11)."""
    ws = story.ws
    with admin_engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text("UPDATE claim_adjustments SET amount = 25.00 WHERE id = :i"), {"i": story.adjustment["id"]}
        )
    _b1_pays_cl4(story)
    doc = _remit(story, "B2-POS", _b2_rows(story, "312.50"), "312.50")
    assert _alloc_rows(admin_engine, doc["id"]) == [(1, story.lines["INV-10020"]["id"], None, D("312.50"))]
    assert finalize(ws.api, doc["id"]).status_code == 200
    assert "INV-10020" not in _cases(story)
