"""db_session: one transaction per request, a new one for the next request, no tenant context."""

from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.api.app import create_app
from rebate.api.deps import db_session
from rebate.core.config import Settings
from tests.conftest import DbUrls


def test_one_transaction_per_request(migrated_db: DbUrls) -> None:
    app = create_app(
        Settings(app_env="ci", secret_key=SecretStr("x" * 40), database_url=SecretStr(migrated_db.app))
    )

    def probe(session: Annotated[Session, Depends(db_session)]) -> dict[str, str | None]:
        first = session.execute(text("SELECT txid_current()::text")).scalar_one()
        second = session.execute(text("SELECT txid_current()::text")).scalar_one()
        assert first == second  # both statements ran in the same transaction
        tenant = session.execute(
            text("SELECT NULLIF(current_setting('app.tenant_id', true), '')")
        ).scalar_one()
        return {"txid": first, "tenant": tenant}

    app.add_api_route("/api/v1/probe", probe)
    with TestClient(app) as client:
        a = client.get("/api/v1/probe").json()
        b = client.get("/api/v1/probe").json()
    assert a["tenant"] is None and b["tenant"] is None
    assert a["txid"] != b["txid"]  # each request gets its own transaction
