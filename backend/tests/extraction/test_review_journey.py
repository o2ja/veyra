"""The contract-review journey the UI drives (`/extractions/:id`), through the real API and database: a read
whose type and rounding are missing, the inline saves, and the counts / checks that must follow each save."""

from contextlib import ExitStack
from typing import Any

import pytest

from tests.extraction.support import draft_of, extract, golden_file, set_ai
from tests.extraction.test_golden import SPA_884
from tests.integration.agreements_support import member, submit, update
from tests.integration.conftest import Workspace


@pytest.fixture
def enabled(ws: Workspace) -> Workspace:
    set_ai(ws, True)
    return ws


def _extraction(ws: Workspace, extraction_id: str) -> dict[str, Any]:
    r = ws.api.get(f"/document-extractions/{extraction_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _save(ws: Workspace, draft_id: str, change: Any) -> dict[str, Any]:
    """What an inline Save does: PUT the edited payload (If-Match), then validate; returns the fresh draft."""
    draft = ws.api.get(f"/agreement-drafts/{draft_id}").json()
    r = update(ws.api, draft, change(draft["payload"]))
    assert r.status_code == 200, r.text
    r = ws.api.post(f"/agreement-drafts/{draft_id}/validate")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _messages(draft: dict[str, Any]) -> list[str]:
    return [e["message"] for e in draft["validation"]["errors"]]


def test_review_journey_type_then_rounding_then_submit(enabled: Workspace) -> None:
    ws = enabled
    ex = extract(ws, golden_file(ws, SPA_884))
    draft_id = draft_of(ws, ex)["id"]
    baseline = ex["needs_count"]
    assert ex["validation"]["valid"] and baseline is not None

    # 1. the read lost its type and rounding (what the user sees when the document does not state them)
    def lose(p: dict[str, Any]) -> dict[str, Any]:
        p["header"].pop("agreement_type")
        p["rules"].pop("type")
        p["rules"].pop("rounding")
        return p

    broken = _save(ws, draft_id, lose)
    assert any("discriminator" in m for m in _messages(broken)), broken["validation"]
    after = _extraction(ws, ex["id"])
    assert after["needs_count"] > baseline  # the extraction reads the draft's live checks, not a stale copy
    assert not after["validation"]["valid"]

    # 2. choose the type: header.agreement_type and rules.type are saved together
    def choose(p: dict[str, Any]) -> dict[str, Any]:
        p["header"]["agreement_type"] = "spa"
        p["rules"]["type"] = "spa"
        return p

    typed = _save(ws, draft_id, choose)
    assert not any("discriminator" in m for m in _messages(typed)), typed["validation"]
    assert {e["field"] for e in typed["validation"]["errors"]} >= {"payload.rules.rounding"}
    assert _extraction(ws, ex["id"])["needs_count"] > baseline
    assert submit(ws.api, draft_id).status_code == 422  # still not submittable

    # 3. fill rounding: nothing is left to decide, the counts match the database again, submit is possible
    def rounding(p: dict[str, Any]) -> dict[str, Any]:
        p["rules"]["rounding"] = {"mode": "half_up", "scale": 2, "timing": "per_line"}
        return p

    done = _save(ws, draft_id, rounding)
    assert done["validation"]["valid"], done["validation"]
    fresh = _extraction(ws, ex["id"])
    assert fresh["needs_count"] == baseline and fresh["validation"]["valid"]

    # 4. submit for approval
    acks = [w["id"] for w in done["validation"]["warnings"]]
    with ExitStack():
        r = submit(ws.api, draft_id, acks)
    assert r.status_code == 200, r.text
    assert ws.api.get(f"/agreement-drafts/{draft_id}").json()["status"] == "pending_approval"
    assert _extraction(ws, ex["id"])["draft_status"] == "pending_approval"


def test_a_viewer_reads_the_same_live_counts(enabled: Workspace) -> None:
    ws = enabled
    ex = extract(ws, golden_file(ws, SPA_884))
    draft_id = draft_of(ws, ex)["id"]
    _save(ws, draft_id, lambda p: {**p, "rules": {k: v for k, v in p["rules"].items() if k != "rounding"}})
    with ExitStack() as stack:
        viewer = member(stack, ws.app, ws.api, "viewer")
        seen = viewer.get(f"/document-extractions/{ex['id']}").json()
    assert seen["needs_count"] > ex["needs_count"] and not seen["validation"]["valid"]
