"""Failure injection through the full job: document problems, OCR, provider errors, malformed output, caps,
missing key. A failed extraction is a visible state with a reason; nothing blocks the manual path."""

import io
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw, ImageFont

from tests.conftest import DbUrls
from tests.extraction.conftest import ai_overrides
from tests.extraction.pdfgen import Run, build_pdf
from tests.extraction.replay import Replay, error, ok, timeout
from tests.extraction.support import agreement_file, draft_of, extract, golden_file, set_ai
from tests.extraction.test_golden import SPA_884
from tests.integration.conftest import Workspace
from tests.support import FakeClock, drain, make_app

GOOD: dict[str, Any] = {
    "schema_version": 1,
    "header": {},
    "rules": {},
    "customers": [],
    "items": [],
    "tiers": [],
}


@pytest.fixture
def enabled(ws: Workspace) -> Workspace:
    set_ai(ws, True)
    return ws


def _manual(ws: Workspace, ex: dict[str, Any], code: str, replay: Replay) -> None:
    assert ex["status"] == "needs_manual_entry" and ex["error_code"] == code, ex
    assert replay.calls == []  # nothing was sent to a provider
    draft = draft_of(ws, ex)
    assert draft["status"] == "needs_manual_entry" and draft["source"] == "ai_extraction"
    # the reviewer can enter the agreement by hand in that very draft
    r = ws.api.put(
        f"/agreement-drafts/{draft['id']}",
        json={"payload": {"payload_schema_version": 1}},
        headers={"If-Match": f'"{draft["revision"]}"'},
    )
    assert r.status_code == 200 and r.json()["status"] == "draft"


def test_encrypted_pdf_never_gets_past_the_upload_scan(enabled: Workspace, replay: Replay) -> None:
    pdf = build_pdf([[Run("Agreement SPA-ENC-1")]], user_password=b"secret", owner_password=b"owner")
    r = enabled.api.client.post(
        "/api/v1/files",
        files={"file": ("enc.pdf", pdf, "application/pdf")},
        data={"kind": "agreement"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": enabled.api.csrf()},
    )
    drain(enabled.app)
    f = enabled.api.get(f"/files/{r.json()['id']}").json()
    assert f["scan_status"] == "infected"  # ClamAV heuristics quarantine encrypted PDFs (SECURITY.md §4)
    refused = enabled.api.post("/document-extractions", json={"file_id": f["id"]})
    assert refused.status_code == 409 and replay.calls == []


def test_corrupt_pdf_needs_manual_entry(enabled: Workspace, replay: Replay) -> None:
    ex = extract(enabled, agreement_file(enabled, "bad.pdf", b"%PDF-1.4\nthis is not a pdf at all\n%%EOF\n"))
    assert ex["error_code"] == "corrupt"
    _manual(enabled, ex, "corrupt", replay)


def test_pdf_without_any_text_and_without_ocr_hits_needs_manual_entry(
    enabled: Workspace, replay: Replay
) -> None:
    ex = extract(enabled, agreement_file(enabled, "blank.pdf", build_pdf([[]])))
    _manual(enabled, ex, "no_text", replay)


def _scanned_pdf() -> bytes:
    """A page that is only a picture of text (what a scanner produces): no text layer at all."""
    img = Image.new("L", (1700, 700), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=64)
    for n, line in enumerate(
        (
            "SPECIAL PRICING AGREEMENT",
            "Agreement No. SPA-OCR-2026-001",
            "Effective January 1, 2026 through December 31, 2026",
        )
    ):
        draw.text((60, 60 + n * 150), line, fill=0, font=font)
    out = io.BytesIO()
    img.save(out, "PDF", resolution=200.0)
    return out.getvalue()


def test_scanned_page_is_ocred_and_its_fields_are_capped_at_medium(
    enabled: Workspace, replay: Replay
) -> None:
    quote = "Agreement No. SPA-OCR-2026-001"
    period = "Effective January 1, 2026 through December 31, 2026"
    answer = {
        **GOOD,
        "header": {
            "external_number": {"value": "SPA-OCR-2026-001", "evidence": {"page": 1, "quote": quote}},
            "agreement_type": {"value": "spa", "evidence": {"page": 1, "quote": "SPECIAL PRICING AGREEMENT"}},
            "valid_from": {"value": "2026-01-01", "evidence": {"page": 1, "quote": period}},
            "valid_to": {"value": "2026-12-31", "evidence": {"page": 1, "quote": period}},
        },
    }
    replay.script.extend([ok(answer), ok(answer)])
    ex = extract(enabled, agreement_file(enabled, "scan.pdf", _scanned_pdf()))
    assert ex["status"] == "completed" and ex["text_method"] == "ocr", (ex["error_code"], ex["error_detail"])
    sent = replay.calls[0]["messages"][0]["content"]
    assert "SPA-OCR-2026-001" in sent  # the model saw the OCR text
    by_key = {f["key"]: f for f in ex["result"]["fields"]}
    number = by_key["header.external_number"]
    assert number["value"] == "SPA-OCR-2026-001" and number["confidence"] == "medium"
    assert "ocr_text" in number["reasons"]
    assert by_key["header.valid_to"]["confidence"] == "medium"  # a critical field read from OCR text


def test_provider_outage_is_retried_by_the_job_then_fails_visibly(
    enabled: Workspace, replay: Replay, clk: FakeClock
) -> None:
    replay.script.extend([error(503)] * 12)  # 3 job attempts x 4 call attempts
    fid = golden_file(enabled, SPA_884)
    r = enabled.api.post("/document-extractions", json={"file_id": fid})
    assert r.status_code == 202
    for attempt in range(3):
        drain(enabled.app)
        ex = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
        if attempt < 2:
            assert ex["status"] == "processing", ex  # queued for a later retry, not yet failed
            clk.advance(120)  # past the job backoff, inside the session idle timeout
    assert ex["status"] == "failed" and ex["error_code"] == "provider_unavailable"
    assert len(replay.calls) == 12 and ex["draft_id"] is None
    # manual entry is unaffected
    assert enabled.api.post("/agreement-drafts", json={"source": "manual"}).status_code == 201


def test_a_retry_does_not_pay_for_a_finished_pass_twice(
    enabled: Workspace, replay: Replay, clk: FakeClock
) -> None:
    fid = golden_file(enabled, SPA_884)
    good_a = Replay.recorded({"system": "x", "messages": [{"content": "SPA-VOLT-2026-884"}]}).json()
    replay.script.append(lambda _b: httpx2.Response(200, json=good_a))
    replay.script.extend([error(500)] * 4)  # pass B: the provider is down for the whole first job attempt
    r = enabled.api.post("/document-extractions", json={"file_id": fid})
    drain(enabled.app)
    clk.advance(120)  # past the job backoff, inside the session idle timeout
    drain(enabled.app)  # second attempt: pass A is reused, only pass B is called (recorded answer)
    ex = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert ex["status"] == "completed", ex
    assert len(replay.calls) == 1 + 4 + 1


def test_malformed_output_gets_one_repair_then_fails_without_a_draft(
    enabled: Workspace, replay: Replay
) -> None:
    replay.script.extend([ok({"nonsense": True}), ok({"still": "wrong"})])
    ex = extract(enabled, golden_file(enabled, SPA_884))
    assert ex["status"] == "failed" and ex["error_code"] == "invalid_output" and ex["draft_id"] is None
    assert len(replay.calls) == 2


def test_timeouts_exhaust_into_a_failed_extraction(
    enabled: Workspace, replay: Replay, clk: FakeClock
) -> None:
    replay.script.extend([timeout] * 12)
    fid = golden_file(enabled, SPA_884)
    r = enabled.api.post("/document-extractions", json={"file_id": fid})
    for _ in range(3):
        drain(enabled.app)
        clk.advance(120)  # past the job backoff, inside the session idle timeout
    ex = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert ex["status"] == "failed" and ex["error_code"] == "provider_unavailable"


def test_workspace_that_switched_ai_off_after_queueing_makes_no_call(
    enabled: Workspace, replay: Replay
) -> None:
    fid = golden_file(enabled, SPA_884)
    r = enabled.api.post("/document-extractions", json={"file_id": fid})
    set_ai(enabled, False)
    drain(enabled.app)
    ex = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert ex["status"] == "needs_manual_entry" and ex["error_code"] == "ai_disabled"
    assert replay.calls == []


# ---- caps and configuration need their own app settings ---------------------------------------------------


@pytest.fixture
def capped_app(migrated_db: DbUrls, replay: Replay) -> FastAPI:
    # pass A costs 4200 x $3 + 1100 x $15 per million = 0.0291, pass B 0.03015: one extraction = 0.05925
    return make_app(migrated_db.app, **ai_overrides(migrated_db, ai_daily_cost_cap_usd=Decimal("0.05")))


def test_daily_cost_cap_stops_the_next_extraction_before_any_call(
    capped_app: FastAPI, migrated_db: DbUrls, replay: Replay, clk: FakeClock
) -> None:
    from tests.integration.conftest import new_workspace
    from tests.support import api_client

    with api_client(capped_app) as api:
        ws = new_workspace(capped_app, api)
        set_ai(ws, True)
        fid = golden_file(ws, SPA_884)
        first = extract(ws, fid)
        assert first["status"] == "completed" and len(replay.calls) == 2
        second = extract(ws, fid, rerun=True)
        assert second["status"] == "failed" and second["error_code"] == "daily_cap_reached"
        assert len(replay.calls) == 2 and second["draft_id"] is None
        usage = ws.api.get("/ai/usage").json()
        assert usage["today"]["cost_usd"] == "0.059250" and usage["today"]["cost_cap_usd"] == "0.05"


def test_missing_provider_key_fails_visibly(migrated_db: DbUrls, replay: Replay) -> None:
    from tests.integration.conftest import new_workspace
    from tests.support import api_client

    app = make_app(migrated_db.app, **{**ai_overrides(migrated_db), "anthropic_api_key": None})
    with api_client(app) as api:
        ws = new_workspace(app, api)
        set_ai(ws, True)
        ex = extract(ws, golden_file(ws, SPA_884))
        assert ex["status"] == "failed" and ex["error_code"] == "provider_not_configured"
        assert replay.calls == []


def test_gateway_database_url_missing_dead_letters_the_job(migrated_db: DbUrls, replay: Replay) -> None:
    from tests.integration.conftest import new_workspace
    from tests.support import api_client

    app = make_app(migrated_db.app)  # no DATABASE_URL_AI
    with api_client(app) as api:
        ws = new_workspace(app, api)
        set_ai(ws, True)
        r = ws.api.post("/document-extractions", json={"file_id": golden_file(ws, SPA_884)})
        drain(app)
        assert ws.api.get(f"/document-extractions/{r.json()['id']}").json()["status"] == "queued"
        failed = ws.api.get("/jobs", params={"status": "failed"}).json()["data"]
        assert any(j["kind"] == "extract_agreement" for j in failed)
        assert replay.calls == []
