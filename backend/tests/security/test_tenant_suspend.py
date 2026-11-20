"""Tenant kill switch: a suspended workspace is refused on every request; reactivation restores it."""

from sqlalchemy import create_engine, text

from tests.conftest import DbUrls
from tests.security.flows import full_admin
from tests.security.test_admin_flows import invite
from tests.support import STRONG_PASSWORD, Api, unique_email


def _set_status(db: DbUrls, workspace: str, status: str) -> None:
    engine = create_engine(db.admin)
    with engine.begin() as conn:
        n = conn.execute(
            text("UPDATE tenants SET status = :s WHERE name = :n"), {"s": status, "n": workspace}
        ).rowcount
    engine.dispose()
    assert n == 1


def test_suspended_tenant_blocks_existing_session_and_reactivation_restores(
    migrated_db: DbUrls, api: Api
) -> None:
    workspace = unique_email("ws-suspend")
    full_admin(api, unique_email("susp"), workspace)
    assert api.get("/settings").status_code == 200

    _set_status(migrated_db, workspace, "suspended")
    for path in ("/settings", "/users"):
        r = api.get(path)
        assert r.status_code == 403, (path, r.text)
        assert r.json()["error"]["code"] == "tenant_suspended"
    assert "suspended" in api.get("/settings").json()["error"]["message"]

    _set_status(migrated_db, workspace, "active")
    assert api.get("/settings").status_code == 200


def test_invitation_to_suspended_tenant_is_refused(migrated_db: DbUrls, api: Api, api2: Api) -> None:
    workspace = unique_email("ws-invite")
    full_admin(api, unique_email("inv"), workspace)
    token = invite(api, unique_email("guest"), "viewer")

    _set_status(migrated_db, workspace, "suspended")
    body = {"token": token, "full_name": "Guest", "password": STRONG_PASSWORD}
    r = api2.post("/invitations/accept", json=body)
    assert r.status_code == 410 and r.json()["error"]["code"] == "invitation_expired"

    _set_status(migrated_db, workspace, "active")
    assert api2.post("/invitations/accept", json=body).status_code == 200
