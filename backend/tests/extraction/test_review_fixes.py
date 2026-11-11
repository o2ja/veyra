"""Independent review of PR 8 (S1-S4 and minors): each test fails without its fix."""

import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from rebate.core.db import make_engine
from rebate.modules.extraction import gateway, prompts, service
from tests.conftest import DbUrls
from tests.extraction.conftest import ai_overrides
from tests.extraction.replay import Replay, error, ok
from tests.extraction.support import agreement_file, golden_file, set_ai, start
from tests.extraction.test_gateway import SYSTEM, USER, VALID, Rig, _validate
from tests.extraction.test_golden import SPA_884
from tests.integration.conftest import Workspace, clean_file, new_workspace
from tests.integration.conftest import start as start_import
from tests.support import FakeClock, api_client, drain, make_app

ZERO = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def enabled(ws: Workspace) -> Workspace:
    set_ai(ws, True)
    return ws


def _finished_events(ws: Workspace, extraction_id: str) -> list[dict[str, Any]]:
    events = ws.api.get("/audit-log", params={"limit": 200}).json()["data"]
    return [e for e in events if e["entity_id"] == extraction_id and e["action"] == "extraction.finished"]


def _drafts_of(ws: Workspace, extraction_id: str) -> list[str]:
    rows = ws.api.get("/agreement-drafts", params={"limit": 200}).json()["data"]
    return [d["id"] for d in rows if d.get("extraction_id") == extraction_id]


def _fail_final_write_once(monkeypatch: pytest.MonkeyPatch, status: str) -> list[int]:
    real = service._write
    hits: list[int] = []

    def flaky(engine: Any, tenant: UUID, extraction_id: UUID, **columns: Any) -> None:
        if columns.get("status") == status and not hits:
            hits.append(1)
            raise RuntimeError("simulated crash before the final status is written")
        real(engine, tenant, extraction_id, **columns)

    monkeypatch.setattr(service, "_write", flaky)
    return hits


# ---- S1: the final status is written last; draft + audit are durable first ------------------------------------


def test_s1_crash_before_the_final_write_keeps_draft_and_audit_and_the_retry_is_idempotent(
    enabled: Workspace, monkeypatch: pytest.MonkeyPatch, clk: FakeClock
) -> None:
    hits = _fail_final_write_once(monkeypatch, "completed")
    r = start(enabled, golden_file(enabled, SPA_884))
    drain(enabled.app)
    assert hits == [1]
    mid = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert mid["status"] == "processing" and mid["draft_id"] is not None  # durable, status not final yet
    assert len(_finished_events(enabled, r.json()["id"])) == 1
    clk.advance(120)
    drain(enabled.app)
    done = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert done["status"] == "completed" and done["result"]["draft_id"] == mid["draft_id"]
    assert len(_drafts_of(enabled, r.json()["id"])) == 1  # no duplicate draft
    assert len(_finished_events(enabled, r.json()["id"])) == 1  # no duplicate audit entry


def test_s1_same_for_the_manual_entry_draft(
    enabled: Workspace, monkeypatch: pytest.MonkeyPatch, clk: FakeClock
) -> None:
    hits = _fail_final_write_once(monkeypatch, "needs_manual_entry")
    fid = agreement_file(enabled, "bad.pdf", b"%PDF-1.4\nnot a pdf\n%%EOF\n")
    r = start(enabled, fid)
    drain(enabled.app)
    assert hits == [1]
    assert (
        len(_drafts_of(enabled, r.json()["id"])) == 1 and len(_finished_events(enabled, r.json()["id"])) == 1
    )
    clk.advance(120)
    drain(enabled.app)
    done = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert done["status"] == "needs_manual_entry" and done["error_code"] == "corrupt"
    assert (
        len(_drafts_of(enabled, r.json()["id"])) == 1 and len(_finished_events(enabled, r.json()["id"])) == 1
    )


def test_s1_a_completed_row_without_its_draft_is_repaired(enabled: Workspace, migrated_db: DbUrls) -> None:
    """Rows written by an older build (final status but no draft): the job rebuilds draft and audit once."""
    from tests.extraction.support import extract

    ex = extract(enabled, golden_file(enabled, SPA_884))
    assert ex["draft_id"]
    assert len(_finished_events(enabled, ex["id"])) == 1
    drain(enabled.app)  # a repeated job run is a no-op
    assert len(_drafts_of(enabled, ex["id"])) == 1 and len(_finished_events(enabled, ex["id"])) == 1


# ---- S2: unfinished rows never block a file --------------------------------------------------------------------


def test_s2_stale_unfinished_row_is_failed_on_the_next_request(
    enabled: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = golden_file(enabled, SPA_884)
    first = start(enabled, fid)
    assert first.status_code == 202
    blocked = start(enabled, fid)
    assert blocked.status_code == 200 and blocked.json()["id"] == first.json()["id"]  # still active: reused
    monkeypatch.setattr(service, "STALE_AFTER", timedelta(seconds=-1))  # everything unfinished is now stale
    again = start(enabled, fid)
    assert again.status_code == 202 and again.json()["id"] != first.json()["id"]
    old = enabled.api.get(f"/document-extractions/{first.json()['id']}").json()
    assert old["status"] == "failed" and old["error_code"] == "stale"
    monkeypatch.undo()
    drain(enabled.app)  # the stale row's job is a no-op, the new one completes
    assert enabled.api.get(f"/document-extractions/{again.json()['id']}").json()["status"] == "completed"
    assert enabled.api.get(f"/document-extractions/{first.json()['id']}").json()["status"] == "failed"


def test_s2_an_unexpected_exception_dead_letters_into_a_failed_row_and_frees_the_file(
    enabled: Workspace, monkeypatch: pytest.MonkeyPatch, clk: FakeClock
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service, "_master_names", boom)
    fid = golden_file(enabled, SPA_884)
    r = start(enabled, fid)
    for _ in range(3):  # 3 job attempts
        drain(enabled.app)
        clk.advance(120)
    ex = enabled.api.get(f"/document-extractions/{r.json()['id']}").json()
    assert ex["status"] == "failed" and ex["error_code"] == "job_failed", ex
    monkeypatch.undo()
    again = start(enabled, fid)  # the file is not blocked
    assert again.status_code == 202 and again.json()["id"] != r.json()["id"]


# ---- S3: rate limit, concurrency cap, mapping outside the request transaction ------------------------------------


def _app(migrated_db: DbUrls, **extra: Any) -> FastAPI:
    return make_app(migrated_db.app, **ai_overrides(migrated_db, **extra))


def test_s3_ai_routes_are_rate_limited_per_workspace(migrated_db: DbUrls, replay: Replay) -> None:
    app = _app(migrated_db, ai_requests_per_minute=3)
    with api_client(app) as api:
        ws = new_workspace(app, api, master=False)
        set_ai(ws, True)
        for _ in range(3):
            assert ws.api.post("/document-extractions", json={"file_id": ZERO}).status_code == 404
        r = ws.api.post("/document-extractions", json={"file_id": ZERO})
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
        assert int(r.headers["Retry-After"]) >= 1
        # suggest-mapping has its own counter: three more are allowed, the fourth is refused
        for _ in range(3):
            assert ws.api.post(f"/imports/{ZERO}/suggest-mapping").status_code == 404
        assert ws.api.post(f"/imports/{ZERO}/suggest-mapping").status_code == 429
        assert ws.api.get("/ai/usage").status_code == 200  # reads are not limited by this counter
    assert replay.calls == []


def test_s3_concurrent_extractions_per_workspace_are_capped(migrated_db: DbUrls, replay: Replay) -> None:
    app = _app(migrated_db, ai_max_active_extractions=1)
    with api_client(app) as api:
        ws = new_workspace(app, api, master=False)
        set_ai(ws, True)
        first = start(ws, golden_file(ws, SPA_884))
        assert first.status_code == 202
        other = golden_file(ws, "SYNTHETIC_rebate_voltara_REB-VOLT-2026")
        r = start(ws, other)
        assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_extractions"
        drain(app)  # the first finishes; capacity is free again
        assert start(ws, other).status_code == 202


def _open_app_transactions(admin: DbUrls) -> int:
    engine = make_engine(admin.admin)
    try:
        with engine.connect() as c:
            return int(
                c.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename = 'rebate_app' "
                        "AND datname = current_database() AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _csv_import(ws: Workspace, header: str = "Cust No,Company,Terms\n") -> dict[str, Any]:
    body = (header + "".join(f"C{n:03d},Company {n},Net 30\n" for n in range(1, 4))).encode()
    return start_import(ws, clean_file(ws, "m.csv", body), "customers")


def test_s3_mapping_does_not_hold_a_database_transaction_during_the_provider_call(
    enabled: Workspace, replay: Replay, migrated_db: DbUrls
) -> None:
    batch = _csv_import(enabled)
    fields = [f["name"] for f in enabled.api.get(f"/imports/{batch['id']}/preview").json()["fields"]]
    seen: list[int] = []

    def during_call(_body: dict[str, Any]) -> httpx2.Response:
        seen.append(_open_app_transactions(migrated_db))
        return ok({"mapping": {fields[0]: "Cust No"}}, tool="record_mapping")(_body)

    replay.script.append(during_call)
    r = enabled.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 200, r.text
    assert seen == [0]  # no rebate_app session idles in a transaction while the model is being called
    assert enabled.api.get(f"/imports/{batch['id']}").status_code == 200  # the session still works afterwards


def test_s3_mapping_is_time_bounded(enabled: Workspace, replay: Replay) -> None:
    batch = _csv_import(enabled)
    replay.script.extend([error(503)] * 5)
    r = enabled.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"
    assert len(replay.calls) == 2  # ai_mapping_max_attempts, not the 4 attempts of the background path


# ---- minors --------------------------------------------------------------------------------------------------


def test_m5_concurrent_calls_cannot_overspend_the_cap(
    ws: Workspace, replay: Replay, migrated_db: DbUrls
) -> None:
    rig = Rig(migrated_db, replay, UUID(ws.tenant_id), ai_daily_cost_cap_usd=Decimal("0.006"))
    started, release = threading.Event(), threading.Event()

    def slow(body: dict[str, Any]) -> httpx2.Response:
        started.set()
        assert release.wait(10)
        return ok(VALID)(body)

    replay.script.append(slow)
    outcome: dict[str, Any] = {}

    def second() -> None:
        try:
            rig.call()
        except gateway.GatewayError as exc:
            outcome["error"] = exc

    first = threading.Thread(target=rig.call)
    first.start()
    assert started.wait(10)
    t = threading.Thread(target=second)
    t.start()  # blocks on the tenant's advisory lock until the first call has written its ledger row
    t.join(0.5)
    assert t.is_alive()
    release.set()
    first.join(10)
    t.join(10)
    assert isinstance(outcome.get("error"), gateway.CapReached)
    assert len(replay.calls) == 1  # without the lock both pass the check and both call the provider


def test_m6_sdk_failures_become_a_ledger_row_and_a_gateway_error(rig_: Rig, replay: Replay) -> None:
    replay.script.append(lambda _b: httpx2.Response(200, content=b"<html>not json</html>"))
    with pytest.raises(gateway.ProviderFailure):
        rig_.call()
    (row,) = rig_.ledger()
    assert row["status"] == "error" and row["error_code"].startswith("sdk_")


def test_m6_mapping_turns_it_into_503_not_500(enabled: Workspace, replay: Replay) -> None:
    batch = _csv_import(enabled)
    replay.script.append(lambda _b: httpx2.Response(200, content=b"<html>not json</html>"))
    r = enabled.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"


def test_m7_mapping_sample_masks_and_bounds_headers(enabled: Workspace, replay: Replay) -> None:
    card = "4111111111111111"
    headers = [f"Col {n}" for n in range(60)]
    headers[0] = f"Card {card}"
    body = (",".join(headers) + "\n" + ",".join(f"v{n}" for n in range(60)) + "\n").encode()
    batch = start_import(enabled, clean_file(enabled, "wide.csv", body), "customers")
    replay.script.append(ok({"mapping": {}}, tool="record_mapping"))
    r = enabled.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 200, r.text
    sent = replay.calls[0]["messages"][0]["content"]
    columns = next(line for line in sent.split("\n") if line.startswith("Columns")).split(": ", 1)[1]
    assert card not in sent and "[card number removed]" in columns
    assert len(columns.split(" | ")) == 50 and "Col 59" not in sent


def test_m8_extractor_notes_are_bounded(enabled: Workspace, replay: Replay) -> None:
    def noisy(body: dict[str, Any]) -> httpx2.Response:
        resp = Replay.recorded(body)
        data = resp.json()
        data["content"][0]["input"]["extractor_notes"] = ["x" * 5000 for _ in range(50)]
        return httpx2.Response(200, json=data)

    replay.script.extend([noisy, noisy])
    from tests.extraction.support import extract

    ex = extract(enabled, golden_file(enabled, SPA_884))
    notes = ex["result"]["notes"]["a"]
    assert len(notes) == 20 and all(len(n) == 500 for n in notes)


def test_m9_every_invocation_is_audited_without_content(enabled: Workspace, replay: Replay) -> None:
    from tests.extraction.support import extract

    ex = extract(enabled, golden_file(enabled, SPA_884))
    events = [
        e
        for e in enabled.api.get("/audit-log", params={"limit": 200}).json()["data"]
        if e["action"] == "ai.invocation"
    ]
    assert sorted(e["after"]["purpose"] for e in events) == ["agreement_pass_a", "agreement_pass_b"]
    a = next(e for e in events if e["after"]["purpose"] == "agreement_pass_a")["after"]
    assert (a["status"], a["cost_usd"], a["usage_in"], a["usage_out"]) == ("ok", "0.029100", 4200, 1100)
    assert a["extraction_id"] == ex["id"]
    assert "SPA-VOLT-2026-884" not in str(events)  # no document content


def test_m9_failed_calls_are_audited_too(enabled: Workspace, replay: Replay) -> None:
    batch = _csv_import(enabled)
    replay.script.append(error(400))
    assert enabled.api.post(f"/imports/{batch['id']}/suggest-mapping").status_code == 503
    events = [
        e
        for e in enabled.api.get("/audit-log", params={"limit": 200}).json()["data"]
        if e["action"] == "ai.invocation"
    ]
    assert [(e["after"]["purpose"], e["after"]["status"], e["after"]["failure"]) for e in events] == [
        ("mapping", "error", "http_400")
    ]


def test_m10_the_source_does_not_import_httpx2_directly() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    offenders = [str(p) for p in src.rglob("*.py") if "httpx2" in p.read_text(encoding="utf-8")]
    assert [o for o in offenders if "extraction" in o] == []


def test_m11_repair_purpose_is_per_caller(rig_: Rig, replay: Replay) -> None:
    replay.script.extend([ok({"bogus": 1}), ok(VALID)])
    out = rig_.gw.call(
        ai_enabled=True,
        tenant_id=rig_.tenant,
        extraction_id=None,
        purpose="mapping",
        model="claude-sonnet-5-5",
        prompt_version="mapping-v1",
        system=SYSTEM,
        user=USER,
        tool=prompts.TOOL_NAME,
        schema=prompts.extraction_schema(),
        validate=_validate,
        repair_purpose="mapping_repair",
    )
    assert out.repaired
    assert [r["purpose"] for r in rig_.ledger()] == ["mapping", "mapping_repair"]


@pytest.fixture
def rig_(ws: Workspace, replay: Replay, migrated_db: DbUrls) -> Rig:
    return Rig(migrated_db, replay, UUID(ws.tenant_id))
