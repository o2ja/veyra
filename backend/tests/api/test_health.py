import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine

from rebate.api.app import create_app
from rebate.core.config import Settings
from rebate.core.db import UnsafeDatabaseRole
from tests.conftest import DbUrls
from tests.support import make_settings

SECRET = SecretStr("x" * 40)


def _app(url: str, env: str = "ci") -> FastAPI:
    if env == "production":
        # In S1 a production Settings object cannot be validated at all: the temporary email sender
        # refuses non-local environments (K-9). Build it unvalidated only to test docs exposure.
        valid = make_settings(url)
        return create_app(Settings.model_construct(**{**valid.model_dump(), "app_env": "production"}))
    return create_app(make_settings(url, app_env=env))


@pytest.fixture
def client(migrated_db: DbUrls) -> Iterator[TestClient]:
    with TestClient(_app(migrated_db.app), raise_server_exceptions=False) as c:
        yield c


def _assert_envelope(body: dict[str, Any], code: str, request_id: str) -> None:
    assert body == {
        "error": {
            "code": code,
            "message": body["error"]["message"],
            "details": [],
            "request_id": request_id,
        }
    }


def test_live(client: TestClient) -> None:
    r = client.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert uuid.UUID(r.headers["X-Request-Id"]).version == 7


def test_ready_checks_database(client: TestClient) -> None:
    assert client.get("/api/v1/health/ready").json() == {"status": "ok"}
    app = client.app
    assert isinstance(app, FastAPI)
    app.state.engine = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 503
    _assert_envelope(r.json(), "unavailable", r.headers["X-Request-Id"])


def test_client_request_id_is_not_trusted(client: TestClient) -> None:
    r = client.get("/api/v1/health/live", headers={"X-Request-Id": "attacker\ninjected"})
    assert r.headers["X-Request-Id"] != "attacker\ninjected"


def test_unknown_route_and_method_use_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    _assert_envelope(r.json(), "not_found", r.headers["X-Request-Id"])
    r = client.post("/api/v1/health/live")
    assert r.status_code == 405
    _assert_envelope(r.json(), "method_not_allowed", r.headers["X-Request-Id"])


def test_unhandled_error_is_generic_500_without_internals(migrated_db: DbUrls) -> None:
    app = _app(migrated_db.app)

    def boom() -> None:
        raise RuntimeError("secret internal detail")

    app.add_api_route("/api/v1/boom", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/boom")
    assert r.status_code == 500
    _assert_envelope(r.json(), "internal_error", r.headers["X-Request-Id"])
    assert "secret internal detail" not in r.text


def test_request_log_is_json_with_request_id_and_no_query(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    r = client.get("/api/v1/health/live?token=should-not-be-logged")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    entry = next(e for e in lines if e.get("event") == "request")
    assert entry["request_id"] == r.headers["X-Request-Id"]
    assert entry["route"] == "/api/v1/health/live"
    assert entry["status"] == 200
    assert "should-not-be-logged" not in json.dumps(lines)


def test_openapi_hidden_in_production(migrated_db: DbUrls) -> None:
    with TestClient(_app(migrated_db.app, env="production")) as c:
        assert c.get("/api/v1/openapi.json").status_code == 404
        assert c.get("/api/v1/health/live").status_code == 200


@pytest.mark.parametrize("which", ["owner", "admin"])
def test_startup_refuses_privileged_database_role(migrated_db: DbUrls, which: str) -> None:
    with pytest.raises(UnsafeDatabaseRole), TestClient(_app(getattr(migrated_db, which))):
        pass
