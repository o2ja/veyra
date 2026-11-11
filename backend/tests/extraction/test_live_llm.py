"""Live-model evaluation (``-m live_llm``, deselected by default; OD-2 unresolved: NEVER run without the
owner's approval of provider and data terms). Needs ``ANTHROPIC_API_KEY`` and network. Only the SYNTHETIC
golden documents are sent. Results are written to ``testdata/eval/live_<model>.json`` for the record
(AI_PROCESSING.md §11) before any prompt, model or provider change."""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from rebate.modules.extraction import gateway
from tests.conftest import DbUrls
from tests.extraction.conftest import ai_overrides
from tests.extraction.harness import run_all
from tests.extraction.support import set_ai
from tests.integration.conftest import Workspace, new_workspace
from tests.support import api_client, make_app
from tests.testdata import ROOT

pytestmark = pytest.mark.live_llm


@pytest.fixture
def live_app(migrated_db: DbUrls, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    monkeypatch.setattr(gateway, "HTTP_CLIENT_FACTORY", None)  # the real transport
    return make_app(
        migrated_db.app,
        **{**ai_overrides(migrated_db), "anthropic_api_key": SecretStr(key), "ai_backoff_base_seconds": 2.0},
    )


@pytest.fixture
def live_ws(live_app: FastAPI) -> Iterator[Workspace]:
    with api_client(live_app) as api:
        yield new_workspace(live_app, api)


def test_live_golden_set(live_ws: Workspace) -> None:
    set_ai(live_ws, True)
    result = run_all(live_ws)
    model = live_ws.app.state.settings.ai_model
    out = Path(ROOT) / "eval" / f"live_{model}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert (
        result["summary"]["hallucinated_values"] == 0
    )  # the acceptance bar; the rest is agreed with the owner
