"""S8 remittance CM-7781 as a PDF: the AI gateway reads the text, the provider answers from a RECORDED response (OD-2:
no live call), deterministic checks gate the human acceptance, and from there the result must equal the CSV's and the
ground truth's (``expected_reconciliation.json``). Tests share a module-scoped workspace and run in file order."""

import copy
import json
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.modules.extraction import gateway
from tests.conftest import DbUrls
from tests.extraction.conftest import ai_overrides
from tests.extraction.replay import Replay, ok
from tests.extraction.support import set_ai
from tests.reconciliation.support import (
    PDF,
    Story,
    build_story,
    check_findings,
    check_matching,
    create,
    ingest,
    listing,
    reconcile,
    upload,
    view,
)
from tests.support import FakeClock, fake_clock, make_app
from tests.testdata import data

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
PDF_BYTES = data("remittance/SYNTHETIC_credit_memo_VOLT_CM-7781.pdf")
RECORDED = Path(__file__).parent / "fixtures" / "recorded" / "SYNTHETIC_credit_memo_VOLT_CM-7781.json"
SHARED: dict[str, Any] = {}


def recorded() -> dict[str, Any]:
    out: dict[str, Any] = json.loads(RECORDED.read_text(encoding="utf-8"))
    out.pop("SYNTHETIC")
    return out


class Env:
    def __init__(self, app: FastAPI, stack: ExitStack, clock: FakeClock, replay: Replay) -> None:
        self.app, self.stack, self.clock, self.replay = app, stack, clock, replay


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    replay = Replay()
    patch = pytest.MonkeyPatch()
    patch.setattr(gateway, "HTTP_CLIENT_FACTORY", replay.client)
    app = make_app(migrated_db.app, **ai_overrides(migrated_db))
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc, replay)
    patch.undo()


@pytest.fixture(scope="module")
def story(env: Env) -> Story:
    return build_story(env.app, env.stack)


def step(payload: dict[str, Any]) -> Any:
    return ok(payload, tool="record_remittance")


def test_a_pdf_remittance_needs_ai_processing_to_be_enabled(story: Story) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    r = create(ws, upload(ws, "cm-7781.pdf", PDF_BYTES, PDF), volt)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_enabled"
    set_ai(ws, True)


def test_figures_the_document_does_not_contain_are_flagged_and_block_acceptance(
    story: Story, env: Env
) -> None:
    """The model answers 2150.00 for line 1 (the document says 2050.00). The figure is not in the text, and the lines
    then add up to 7645.00, not the stated 7545.00: both are reported, acceptance is refused, nothing is matchable."""
    ws, volt = story.ws, story.mfr["VOLT"]
    wrong = copy.deepcopy(recorded())
    wrong["lines"][0]["amount_paid"] = "2150.00"
    env.replay.script.append(step(wrong))
    doc = ingest(ws, upload(ws, "cm-7781-first.pdf", PDF_BYTES + b"\n%x", PDF), volt)
    assert doc["status"] == "pending_review" and doc["document_number"] == "CM-7781"
    ex = ws.api.get(f"/remittances/{doc['id']}/extraction").json()
    codes = sorted((p["field"], p["code"]) for p in ex["problems"])
    assert ("amount_paid", "not_in_document") in codes and ("total_amount", "total_mismatch") in codes
    flagged = [x for x in ex["lines"] if x["issues"]]
    assert [x["line_number"] for x in flagged] == [1]
    assert "7645.00" in next(p for p in ex["problems"] if p["code"] == "total_mismatch")["message"]
    refused = ws.api.post(f"/remittances/{doc['id']}/accept", json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "cannot_accept"
    assert ws.api.get(f"/remittances/{doc['id']}/lines").json()["data"] == []
    assert ws.api.post(f"/remittances/{doc['id']}/void").status_code == 200


def test_the_recorded_extraction_is_staged_for_review(story: Story, env: Env, admin_engine: Engine) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    env.replay.script.append(step(recorded()))
    doc = ingest(ws, upload(ws, "credit_memo_VOLT_CM-7781.pdf", PDF_BYTES, PDF), volt, doc_type="credit_memo")
    assert doc["status"] == "pending_review"  # a person accepts AI-read lines; they are not matchable before
    assert doc["document_number"] == "CM-7781" and doc["document_date"] == "2026-07-31"
    assert Decimal(doc["total_amount"]) == Decimal("7545.00") and doc["extraction_id"]
    ex = ws.api.get(f"/remittances/{doc['id']}/extraction").json()
    assert ex["problems"] == []  # figures found in the document, sum 7545.00 = stated total
    assert [Decimal(x["amount_paid"]) for x in ex["lines"]] == [
        Decimal(v) for v in ("2050.00", "3120.00", "0.00", "200.00", "2100.00", "75.00")
    ]
    assert ws.api.get(f"/remittances/{doc['id']}/lines").json()["data"] == []
    with admin_engine.connect() as conn:
        e = conn.execute(
            text(
                "SELECT status, kind, model, prompt_version, result FROM document_extractions WHERE id = :i"
            ),
            {"i": doc["extraction_id"]},
        ).one()
        invocations = conn.execute(
            text("SELECT purpose, status FROM ai_invocations WHERE extraction_id = :i"),
            {"i": doc["extraction_id"]},
        ).all()
    assert (e.status, e.kind, e.prompt_version) == ("completed", "remittance", "remittance-v1")
    assert [tuple(i) for i in invocations] == [("remittance_pass", "ok")]
    assert e.result["header"]["total_amount"] == "7545.00"
    SHARED["doc"] = doc


def test_a_person_accepts_and_the_result_is_the_ground_truth(story: Story) -> None:
    ws = story.ws
    doc = SHARED["doc"]
    ok_ = ws.api.post(f"/remittances/{doc['id']}/accept", json={})
    assert ok_.status_code == 200 and ok_.json()["status"] == "accepted"
    assert ok_.json()["accepted_by"] == ws.user_id
    lines = listing(ws.api, f"/remittances/{doc['id']}/lines")
    assert len(lines) == 6 and sum(Decimal(x["amount_paid"]) for x in lines) == Decimal("7545.00")
    assert lines[0]["reason_code"] == "rejected_price_mismatch" and lines[2]["reason_code"] == (
        "rejected_quantity_exceeds_authorization"
    )
    reconcile(ws, doc["id"])
    rows = view(ws, doc["id"])
    check_matching(story, rows)
    m5 = rows[5]["matches"][0]
    ws.api.post(f"/matches/{m5['id']}/confirm", headers={"If-Match": f'"{m5["revision"]}"'})
    r = ws.api.post(
        f"/remittances/{doc['id']}/finalize",
        headers={"Idempotency-Key": "00000000-0000-4000-8000-000000000001"},
    )
    assert r.status_code == 200, r.text
    check_findings(story, r.json())


def test_a_duplicate_pdf_remittance_is_refused(story: Story) -> None:
    ws, volt = story.ws, story.mfr["VOLT"]
    again = create(
        ws, upload(ws, "cm-7781-again.pdf", PDF_BYTES + b"\n%y", PDF), volt, document_number="CM-7781"
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate_remittance"
