"""Route-level isolation (kickoff report §6.2, R1-R6, R11). Two workspaces created through the API.

These tests guard the application layer (explicit tenant filters), so they must still pass with RLS
disabled; the negative control checks exactly that.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.routing import APIRoute

from rebate.api.app import MODULE_ROUTERS
from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.security.flows import full_admin
from tests.support import Api, api_client, fake_clock, latest_token, make_app, unique_email


@dataclass
class Side:
    api: Api
    tenant_id: str
    user_id: str
    invitation_id: str
    invitee: str
    file_id: str
    job_id: str


@pytest.fixture(scope="module")
def sides(migrated_db: DbUrls) -> Iterator[tuple[Side, Side]]:
    app = make_app(migrated_db.app)
    storage.ensure_bucket(app.state.settings)
    with fake_clock(), api_client(app) as a, api_client(app) as b:
        out = []
        for api, name in ((a, "Iso Route A (SYNTHETIC)"), (b, "Iso Route B (SYNTHETIC)")):
            full_admin(api, unique_email("isoroute"), workspace=name)
            me = api.get("/auth/me").json()
            invitee = unique_email("isoinvitee")
            inv = api.post("/invitations", json={"email": invitee, "role": "viewer"}).json()
            latest_token(invitee)
            csrf = api.csrf()
            up = api.client.post(
                "/api/v1/files",
                files={"file": (f"{name}.csv", f"a,b\n{name},1\n".encode(), "text/csv")},
                data={"kind": "import"},
                headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
            )
            assert up.status_code == 201, up.text
            job = api.get("/jobs", params={"status": "queued"}).json()["data"][0]
            out.append(
                Side(
                    api, me["tenant"]["id"], me["user"]["id"], inv["id"], invitee, up.json()["id"], job["id"]
                )
            )
        yield out[0], out[1]


# path parameter -> attribute of the *other* tenant used as the attack value
PARAM_SOURCES = {
    "user_id": "user_id",
    "invitation_id": "invitation_id",
    "file_id": "file_id",
    "job_id": "job_id",
}

ID_ROUTES = [
    (route, method)
    for router in MODULE_ROUTERS
    for route in router.routes
    if isinstance(route, APIRoute) and "{" in route.path
    for method in sorted(route.methods or ())
]


def test_r11_every_parameterised_route_is_covered() -> None:
    params = {p for route, _ in ID_ROUTES for p in re.findall(r"\{([^}]+)\}", route.path)}
    missing = params - PARAM_SOURCES.keys()
    assert not missing, f"add an isolation attack value for path parameters {missing}"


def _call(api: Api, method: str, path: str, body: Any) -> Any:
    if method == "GET":
        return api.get(path)
    if method == "DELETE":
        return api.delete(path)
    return (api.post if method == "POST" else api.patch)(path, json=body)


BODIES = {"PATCH": {"role": "viewer"}, "POST": {}}


@pytest.mark.parametrize(("route", "method"), ID_ROUTES, ids=lambda x: getattr(x, "path", x))
def test_r1_foreign_ids_look_exactly_like_missing_ids(
    sides: tuple[Side, Side], route: APIRoute, method: str
) -> None:
    a, b = sides
    foreign = re.sub(r"\{([^}]+)\}", lambda m: getattr(b, PARAM_SOURCES[m.group(1)]), route.path)
    missing = re.sub(r"\{[^}]+\}", "00000000-0000-7000-8000-000000000000", route.path)
    r_foreign = _call(a.api, method, foreign, BODIES.get(method))
    r_missing = _call(a.api, method, missing, BODIES.get(method))
    assert r_foreign.status_code == 404, (
        f"{method} {route.path}: {r_foreign.status_code} {r_foreign.text[:200]}"
    )
    assert r_foreign.json()["error"]["code"] == r_missing.json()["error"]["code"] == "not_found"
    assert r_foreign.json()["error"]["message"] == r_missing.json()["error"]["message"]


def test_r2_r4_lists_never_contain_the_other_tenant(sides: tuple[Side, Side]) -> None:
    a, b = sides
    assert b.file_id not in {f["id"] for f in a.api.get("/files").json()["data"]}
    for status in ("queued", "running", "succeeded", "failed"):
        assert b.job_id not in {j["id"] for j in a.api.get("/jobs", params={"status": status}).json()["data"]}
    users = a.api.get("/users").json()["data"]
    assert b.user_id not in {u["user_id"] for u in users}
    invitations = a.api.get("/invitations").json()["data"]
    assert b.invitation_id not in {i["id"] for i in invitations}
    assert b.invitee not in {i["email"] for i in invitations}
    for params in (
        {},
        {"actor_user_id": b.user_id},
        {"entity_id": b.tenant_id},
        {"entity_id": b.invitation_id},
    ):
        entries = a.api.get("/audit-log", params=params).json()["data"]
        assert all(e["actor_user_id"] != b.user_id and e["entity_id"] != b.tenant_id for e in entries), params
    own = a.api.get("/audit-log", params={"actor_user_id": a.user_id}).json()["data"]
    assert own, "A must see its own audit entries (otherwise R4 proves nothing)"


def test_r3_foreign_mutations_leave_the_other_tenant_unchanged(sides: tuple[Side, Side]) -> None:
    a, b = sides
    assert a.api.patch(f"/users/{b.user_id}", json={"role": "viewer"}).status_code == 404
    assert a.api.delete(f"/invitations/{b.invitation_id}").status_code == 404
    assert b.api.get("/auth/me").json()["role"] == "admin"
    assert any(
        i["id"] == b.invitation_id and i["revoked_at"] is None
        for i in b.api.get("/invitations").json()["data"]
    )


def test_r5_cannot_switch_into_a_foreign_workspace(sides: tuple[Side, Side]) -> None:
    a, b = sides
    before = a.api.client.cookies.get("__Host-session")
    r = a.api.post("/auth/switch-tenant", json={"tenant_id": b.tenant_id})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert a.api.client.cookies.get("__Host-session") == before
    assert a.api.get("/auth/me").json()["tenant"]["id"] == a.tenant_id


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/settings", {"tenant_id": "x", "mfa_required": True}),
        ("POST", "/invitations", {"email": "t@example.test", "role": "viewer", "tenant_id": "x"}),
        (
            "POST",
            "/auth/switch-tenant",
            {"tenant_id": "00000000-0000-7000-8000-000000000000", "user_id": "x"},
        ),
    ],
)
def test_r6_client_supplied_tenant_or_extra_fields_are_rejected(
    sides: tuple[Side, Side], method: str, path: str, body: dict[str, Any]
) -> None:
    a, _ = sides
    r = _call(a.api, method, path, body)
    assert r.status_code == 422, r.text


def test_settings_are_the_callers_own(sides: tuple[Side, Side]) -> None:
    a, b = sides
    etag = b.api.get("/settings").headers["ETag"]
    b.api.patch("/settings", json={"retention_years": 9}, headers={"If-Match": etag})
    assert a.api.get("/settings").json()["retention_years"] == 7
    assert b.api.get("/settings").json()["retention_years"] == 9
