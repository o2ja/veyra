"""Opt-in, permissions, usage API, mapping suggestions, and the guarantee that a workspace without AI is fully
functional and that no AI output affects anything before a human approves it."""

import re
from contextlib import ExitStack
from typing import Any

import pytest

from tests.extraction.replay import Replay, ok
from tests.extraction.support import draft_of, extract, golden_file, set_ai, start
from tests.extraction.test_golden import SPA_884
from tests.integration.agreements_support import activate, member, payload
from tests.integration.conftest import Workspace, clean_file, import_file
from tests.integration.conftest import start as start_import


def _no_ai_rows(ws: Workspace) -> None:
    assert ws.api.get("/document-extractions").json()["data"] == []
    assert ws.api.get("/ai/usage").json()["daily"] == []


def test_workspace_without_opt_in_gets_a_clear_state_and_no_provider_call(
    ws: Workspace, replay: Replay
) -> None:
    fid = golden_file(ws, SPA_884)
    r = start(ws, fid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_enabled"
    assert ws.api.get("/ai/usage").json()["enabled"] is False
    _no_ai_rows(ws)
    imp = start_import(ws, clean_file(ws, "m.csv", b"a,b\n1,2\n"), "customers")
    r = ws.api.post(f"/imports/{imp['id']}/suggest-mapping")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_enabled"
    assert replay.calls == []


def test_a_workspace_without_ai_still_imports_and_manages_agreements(ws: Workspace, replay: Replay) -> None:
    """Imports (done by the fixture's master data), a manual agreement through approval: all without AI."""
    assert import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")["status"] == "completed"
    with ExitStack() as stack:
        approver = member(stack, ws.app, ws.api, "approver")
        version = activate(ws.api, approver, payload("SPA-VOLT-2026-884"))
    assert version["status"] == "active"
    assert replay.calls == [] and ws.api.get("/ai/usage").json()["today"]["tokens"] == 0


def test_no_ai_output_affects_agreements_before_a_human_approves(ws: Workspace, replay: Replay) -> None:
    set_ai(ws, True)
    ex = extract(ws, golden_file(ws, SPA_884))
    assert ex["status"] == "completed"
    assert ws.api.get("/agreements").json()["data"] == []  # no agreement, no version exists yet
    draft = draft_of(ws, ex)
    assert draft["status"] == "draft" and draft["resulting_version_id"] is None
    # drafts are the only place the AI result lives, and they are not versions
    assert ws.api.get("/agreement-drafts", params={"status": "approved"}).json()["data"] == []


def test_the_extraction_reads_are_available_to_a_viewer_but_starting_one_is_not(ws: Workspace) -> None:
    set_ai(ws, True)
    with ExitStack() as stack:
        viewer = member(stack, ws.app, ws.api, "viewer")
        analyst = member(stack, ws.app, ws.api, "analyst")
        fid = golden_file(ws, SPA_884)
        denied = viewer.post("/document-extractions", json={"file_id": fid})
        assert denied.status_code == 403
        started = analyst.post("/document-extractions", json={"file_id": fid})
        assert started.status_code == 202
        assert viewer.get(f"/document-extractions/{started.json()['id']}").status_code == 200
        assert viewer.get("/ai/usage").status_code == 200
        assert viewer.post("/imports/00000000-0000-4000-8000-000000000000/suggest-mapping").status_code == 403


def test_unknown_ids_and_bad_bodies(ws: Workspace) -> None:
    set_ai(ws, True)
    zero = "00000000-0000-4000-8000-000000000000"
    assert ws.api.get(f"/document-extractions/{zero}").status_code == 404
    assert start(ws, zero).status_code == 404
    assert (
        ws.api.post("/document-extractions", json={"file_id": zero, "kind": "remittance"}).status_code == 422
    )
    assert ws.api.post("/document-extractions", json={"file_id": zero, "x": 1}).status_code == 422
    assert ws.api.get("/document-extractions", params={"bogus": 1}).status_code == 400


def test_usage_api_reports_calls_tokens_and_cost_from_the_ledger(ws: Workspace, replay: Replay) -> None:
    set_ai(ws, True)
    extract(ws, golden_file(ws, SPA_884))
    usage = ws.api.get("/ai/usage").json()
    assert usage["enabled"] is True and usage["model"] == "claude-sonnet-5-5"
    (day,) = usage["daily"]
    # two passes: 4200 + 4300 input and 1100 + 1150 output tokens (the recorded responses' usage), at $3 / $15
    # per million: 8500 x 3 / 1e6 + 2250 x 15 / 1e6 = 0.0255 + 0.03375
    assert (day["calls"], day["input_tokens"], day["output_tokens"]) == (2, 8500, 2250)
    assert day["cost_usd"] == "0.059250"
    assert usage["today"]["tokens"] == 10750 and usage["today"]["cost_usd"] == "0.059250"


def test_audit_trail_records_the_request_and_the_outcome(ws: Workspace) -> None:
    set_ai(ws, True)
    ex = extract(ws, golden_file(ws, SPA_884))
    events = ws.api.get("/audit-log", params={"limit": 200}).json()["data"]
    mine = [e for e in events if e["entity_id"] == ex["id"]]
    assert {e["action"] for e in mine} == {"extraction.requested", "extraction.finished"}
    finished = next(e for e in mine if e["action"] == "extraction.finished")
    assert finished["actor_type"] == "system" and finished["after"]["draft_id"] == ex["draft_id"]
    created = next(
        e for e in events if e["entity_id"] == ex["draft_id"] and e["action"] == "agreement_draft.created"
    )
    assert created["after"]["extraction_id"] == ex["id"]


# ---- mapping suggestions (S6.6) -----------------------------------------------------------------------------

CARD = "4111111111111111"  # a Luhn-valid test number


def _import_needing_a_mapping(ws: Workspace) -> dict[str, Any]:
    rows = [f"C{n:03d},Company {n},Net 30,{CARD if n == 1 else 'plain'}" for n in range(1, 9)]
    body = ("Cust No,Company,Terms,Remarks\n" + "\n".join(rows) + "\n").encode()
    return start_import(ws, clean_file(ws, "weird.csv", body), "customers")


def test_mapping_suggestion_sends_headers_and_at_most_five_masked_rows(ws: Workspace, replay: Replay) -> None:
    set_ai(ws, True)
    batch = _import_needing_a_mapping(ws)
    fields = [f["name"] for f in ws.api.get(f"/imports/{batch['id']}/preview").json()["fields"]]
    answer = {"mapping": {fields[0]: "Cust No", fields[1]: "Company"}}
    replay.script.append(ok(answer, tool="record_mapping"))
    r = ws.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mapping"][fields[0]] == "Cust No" and out["mapping"][fields[1]] == "Company"
    assert all(v is None for k, v in out["mapping"].items() if k not in fields[:2])
    assert out["source"] == "ai" and out["prompt_version"] == "mapping-v1"
    sent = replay.calls[0]["messages"][0]["content"]
    data_block = re.search(r"<data>\n(.*)\n</data>", sent, re.S)
    assert data_block is not None
    lines = data_block.group(1).split("\n")
    assert len(lines) == 5 and lines[0].startswith("C001") and "C006" not in sent
    assert CARD not in sent and "[card number removed]" in sent
    assert "Cust No | Company | Terms | Remarks" in sent
    assert ws.api.get("/ai/usage").json()["daily"][0]["calls"] == 1


def test_mapping_answer_with_an_unknown_column_is_rejected_after_one_repair(
    ws: Workspace, replay: Replay
) -> None:
    set_ai(ws, True)
    batch = _import_needing_a_mapping(ws)
    fields = [f["name"] for f in ws.api.get(f"/imports/{batch['id']}/preview").json()["fields"]]
    bad = ok({"mapping": {fields[0]: "No Such Column"}}, tool="record_mapping")
    replay.script.extend([bad, bad])
    r = ws.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"
    assert len(replay.calls) == 2


def test_mapping_suggestion_of_a_foreign_batch_is_not_found(ws: Workspace, replay: Replay) -> None:
    set_ai(ws, True)
    zero = "00000000-0000-4000-8000-000000000000"
    assert ws.api.post(f"/imports/{zero}/suggest-mapping").status_code == 404
    assert replay.calls == []


@pytest.mark.parametrize("path", ["/document-extractions", "/ai/usage"])
def test_reads_require_a_session(ws: Workspace, path: str) -> None:
    from fastapi.testclient import TestClient

    with TestClient(ws.app, base_url="https://testserver") as anonymous:
        assert anonymous.get(f"/api/v1{path}").status_code == 401
