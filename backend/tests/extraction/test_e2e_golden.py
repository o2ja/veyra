"""Golden documents through the full job (upload -> scan -> extract_agreement -> draft) with recorded
responses, scored against ``expected_extraction/*``. No value in a draft may contradict the ground truth
(hallucination rate 0); what the document does not state stays empty and flagged."""

from contextlib import ExitStack
from typing import Any

import pytest

from tests.extraction.evaluate import score
from tests.extraction.golden import ground_truth
from tests.extraction.replay import Replay
from tests.extraction.support import draft_of, extract, golden_file, set_ai, start
from tests.extraction.test_golden import BRIGHT, COPPER, INJECT, MISSING, REB_NWND, REB_VOLT, SPA_884
from tests.integration.agreements_support import approve, member, submit
from tests.integration.conftest import Workspace

STEMS = [SPA_884, REB_VOLT, REB_NWND, COPPER, INJECT, MISSING, BRIGHT]
# Items / tiers the ground truth lists must all be found where the document states them.
FULL_COVERAGE = {SPA_884, REB_VOLT, REB_NWND, COPPER, INJECT}


@pytest.fixture
def enabled(ws: Workspace) -> Workspace:
    set_ai(ws, True)
    return ws


@pytest.mark.parametrize("stem", STEMS)
def test_golden_document_through_the_full_job(enabled: Workspace, stem: str) -> None:
    ws, truth = enabled, ground_truth(stem)
    ex = extract(ws, golden_file(ws, stem))
    assert ex["status"] == "completed", ex
    assert ex["prompt_version"] == "agreement-v1" and ex["model"] == "claude-sonnet-5-5"
    draft = draft_of(ws, ex)
    assert draft["source"] == "ai_extraction" and draft["status"] == "draft"
    s = score(truth, draft["payload"], ex["result"])
    assert s["wrong"] == {}, f"{stem}: values contradict the ground truth: {s['wrong']}"
    if stem in FULL_COVERAGE:
        assert {i["sku"] for i in draft["payload"]["items"]} == {i["sku"] for i in truth["items"]}
        assert len(draft["payload"]["tiers"]) == len(truth["tiers"])
        assert s["recall"] >= 0.8, s

    def errors(v: dict[str, Any]) -> set[tuple[str, str]]:
        return {(e["field"], e["code"]) for e in v["errors"]}

    assert ex["validation"]["valid"] == draft["validation"]["valid"]
    assert errors(ex["validation"]) == errors(draft["validation"])
    for f in ex["result"]["fields"]:  # every stored value is backed by evidence, or it is not stored
        if f["value"] is not None:
            assert f["evidence"] is not None and f["evidence"]["quote"], f


def test_clean_spa_draft_validates_and_flows_through_the_shared_approval(enabled: Workspace) -> None:
    ws = enabled
    ex = extract(ws, golden_file(ws, SPA_884))
    draft = draft_of(ws, ex)
    assert draft["validation"]["valid"], draft["validation"]
    with ExitStack() as stack:
        approver = member(stack, ws.app, ws.api, "approver")
        r = submit(ws.api, draft["id"], [w["id"] for w in draft["validation"]["warnings"]])
        assert r.status_code == 200, r.text
        r = approve(approver, draft["id"])
        assert r.status_code == 200, r.text
    after = ws.api.get(f"/agreement-drafts/{draft['id']}").json()
    assert after["status"] == "approved" and after["extraction_id"] == ex["id"]


def test_unresolved_references_are_kept_visible_not_invented(enabled: Workspace) -> None:
    ws = enabled
    draft = draft_of(ws, extract(ws, golden_file(ws, BRIGHT)))
    # the sheet abbreviates customers ("Alder"): no master code is guessed, the printed name stays in the draft
    assert [c["customer_code"] for c in draft["payload"]["customers"]] == ["Alder", "Delta", "Evergreen"]
    assert not draft["validation"]["valid"]


def test_missing_info_document_leaves_unstated_fields_empty_and_flagged(enabled: Workspace) -> None:
    ws = enabled
    ex = extract(ws, golden_file(ws, MISSING))
    draft = draft_of(ws, ex)
    assert "valid_to" not in draft["payload"]["header"] and "date_basis" not in draft["payload"]["rules"]
    by_key = {f["key"]: f for f in ex["result"]["fields"]}
    assert by_key["header.valid_to"]["confidence"] == "low"
    assert "not_stated" in by_key["header.valid_to"]["reasons"]
    assert not draft["validation"]["valid"]  # the validator, not the model, decides what is required


def test_injection_document_is_flagged_and_its_instruction_is_not_in_the_draft(
    enabled: Workspace, replay: Replay
) -> None:
    ws = enabled
    ex = extract(ws, golden_file(ws, INJECT))
    assert {"hidden_text", "tiny_font", "instruction"} <= {i["kind"] for i in ex["result"]["indicators"]}
    draft = draft_of(ws, ex)
    assert {i["sku"]: i["deviated_cost"] for i in draft["payload"]["items"]} == {"BRK-3P-60A": "50.00"}
    first = replay.calls[0]
    assert "UNTRUSTED DATA" in first["system"] and first["tool_choice"]["type"] == "tool"
    assert [t["name"] for t in first["tools"]] == ["record_extraction"]  # no other tools offered


def test_rates_given_as_percentages_in_pass_b_are_converted_by_the_backend(enabled: Workspace) -> None:
    ws = enabled
    ex = extract(ws, golden_file(ws, REB_VOLT))
    draft = draft_of(ws, ex)
    assert [t["rate"] for t in draft["payload"]["tiers"]] == ["0.015", "0.025", "0.035"]
    pass_b = next(f for f in ex["result"]["fields"] if f["key"] == "tiers[1000000].rate")["pass_b"]
    assert pass_b["value"] == "0.025" and pass_b["status"] == "verified"


def test_extraction_is_idempotent_and_rerun_creates_a_new_row(enabled: Workspace, replay: Replay) -> None:
    ws = enabled
    fid = golden_file(ws, SPA_884)
    ex = extract(ws, fid)
    calls = len(replay.calls)
    again = start(ws, fid)
    assert again.status_code == 200 and again.json()["id"] == ex["id"] and len(replay.calls) == calls
    rerun = extract(ws, fid, rerun=True)
    assert rerun["id"] != ex["id"] and len(replay.calls) == calls * 2
