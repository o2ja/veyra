"""Fixture baseline (CI): the golden set through the full job with recorded responses, compared with the
committed ``fixtures/baseline_fixture_metrics.json``. This measures the **deterministic layer** (verification,
parsing, validation, draft building) against answers a correct model would give; it says nothing about a
real model (live baseline pending OD-2). Regenerate after an intended change: ``UPDATE_BASELINE=1``."""

import json
import os
from pathlib import Path

import pytest

from tests.extraction.harness import run_all
from tests.extraction.support import set_ai
from tests.integration.conftest import Workspace

BASELINE = Path(__file__).parent / "fixtures" / "baseline_fixture_metrics.json"


def test_fixture_baseline_metrics(ws: Workspace) -> None:
    set_ai(ws, True)
    result = run_all(ws)
    result["note"] = "recorded-response fixtures; live baseline pending OD-2"
    if os.environ.get("UPDATE_BASELINE") == "1":
        BASELINE.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("baseline rewritten")
    s = result["summary"]
    assert s["completed"] == s["documents"] == 7
    assert s["hallucinated_values"] == 0 and s["precision"] == 1.0  # acceptance: no wrong value survives
    assert result == json.loads(BASELINE.read_text(encoding="utf-8"))
