"""Extraction tests: the S3 integration harness (real API, Postgres, SeaweedFS, ClamAV) with the AI gateway
wired to the restricted role ``rebate_ai_writer`` and the provider answering from recorded responses."""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from rebate.modules.extraction import gateway
from tests.conftest import DbUrls
from tests.extraction.replay import Replay
from tests.integration.conftest import bucket, clk, ws  # noqa: F401  (fixtures reused as they are)
from tests.support import make_app


def ai_url(db_urls: DbUrls) -> str:
    url = make_url(db_urls.app).set(
        username="rebate_ai_writer", password=os.environ["REBATE_AI_WRITER_PASSWORD"]
    )
    return url.render_as_string(hide_password=False)


def ai_overrides(db_urls: DbUrls, **extra: Any) -> dict[str, Any]:
    return {
        "database_url_ai": SecretStr(ai_url(db_urls)),
        "anthropic_api_key": SecretStr("sk-replay-not-a-real-key"),
        "ai_backoff_base_seconds": 0.0,
        "ai_backoff_max_seconds": 0.0,
        **extra,
    }


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> Iterator[Replay]:
    r = Replay()
    monkeypatch.setattr(gateway, "HTTP_CLIENT_FACTORY", r.client)
    yield r


@pytest.fixture
def app(migrated_db: DbUrls, replay: Replay) -> FastAPI:
    return make_app(migrated_db.app, **ai_overrides(migrated_db))
