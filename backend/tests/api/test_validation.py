"""422 envelope for invalid bodies, and money floats rejected at the HTTP boundary."""

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from rebate.core.money import Money
from tests.conftest import DbUrls
from tests.support import make_app


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Money


def test_invalid_body_returns_validation_envelope(migrated_db: DbUrls) -> None:
    app = make_app(migrated_db.app)

    def echo(body: _Body) -> _Body:
        return body

    app.add_api_route("/api/v1/echo", echo, methods=["POST"])
    with TestClient(app) as client:
        ok = client.post("/api/v1/echo", json={"amount": "2200.00"})
        assert ok.status_code == 200
        assert ok.json() == {"amount": "2200.00"}

        r = client.post("/api/v1/echo", json={"amount": 2200.0, "tenant_id": "x"})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"] == r.headers["X-Request-Id"]
    assert {d["field"] for d in error["details"]} == {"amount", "tenant_id"}
    assert all(d["code"] and d["message"] for d in error["details"])
