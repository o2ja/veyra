"""Every route declares its access; the role x route matrix follows the registry (SECURITY.md §3, §6).

* Meta-test: each route in the router registry declares exactly one of public / authenticated /
  require(permission), and the registry covers every path in the OpenAPI document (no stray routes).
* Matrix: for every permission route and every role, allowed roles are never refused with 403 and
  every other role gets 403 ``forbidden``; unauthenticated callers get 401 on every non-public route.
"""

import re
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from rebate.api.app import MODULE_ROUTERS, PUBLIC_ROUTERS
from rebate.api.paths import API_PREFIX
from rebate.core.access import ACCESS_ATTR
from rebate.modules.tenancy import PERMISSIONS, ROLES
from tests.conftest import DbUrls
from tests.security.flows import Account, enable_mfa, full_admin, login
from tests.support import STRONG_PASSWORD, Api, api_client, fake_clock, latest_token, make_app, unique_email


def _markers(dependant: Dependant) -> list[tuple[str, str | None]]:
    found: list[tuple[str, str | None]] = []
    stack = [dependant]
    while stack:
        d = stack.pop()
        marker = getattr(d.call, ACCESS_ATTR, None)
        if marker is not None:
            found.append(marker)
        stack.extend(d.dependencies)
    return found


def _routes() -> Iterator[tuple[APIRoute, tuple[str, str | None]]]:
    for router in (*MODULE_ROUTERS, *PUBLIC_ROUTERS):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            markers = _markers(route.dependant)
            assert len(markers) == 1, f"{route.methods} {route.path}: access declarations {markers}"
            yield route, markers[0]


ROUTES = list(_routes())


def test_every_route_declares_exactly_one_access_rule() -> None:
    assert ROUTES  # the assertions run inside _routes()


def test_registry_covers_every_openapi_path(migrated_db: DbUrls) -> None:
    spec = make_app(migrated_db.app).openapi()
    documented = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    registered = {(m, f"{API_PREFIX}{r.path}") for r, _ in ROUTES for m in (r.methods or ())}
    assert documented == registered


def test_permissions_named_in_routes_exist_in_the_registry() -> None:
    for route, (kind, value) in ROUTES:
        if kind == "permission":
            assert value in PERMISSIONS, f"{route.path}: unknown permission {value}"


def _url(route: APIRoute) -> str:
    return API_PREFIX + re.sub(r"\{[^}]+\}", str(uuid4()), route.path)


def _call(api: Api, method: str, url: str) -> Any:
    path = url.removeprefix(API_PREFIX)
    if method == "GET":
        return api.get(path)
    if method == "DELETE":
        return api.delete(path)
    sender: Callable[..., Any] = api.post if method == "POST" else api.patch
    return sender(path, json={})


@pytest.fixture(scope="module")
def role_clients(migrated_db: DbUrls) -> Iterator[dict[str, Api]]:
    """One workspace with one signed-in, MFA-enrolled user per role."""
    app: FastAPI = make_app(migrated_db.app)
    with fake_clock(), api_client(app) as admin_api:
        full_admin(admin_api, unique_email("matrix-admin"), workspace="Matrix Co (SYNTHETIC)")
        clients: dict[str, Api] = {"admin": admin_api}
        contexts = []
        for role in ("approver", "analyst", "viewer"):
            email = unique_email(f"matrix-{role}")
            assert admin_api.post("/invitations", json={"email": email, "role": role}).status_code == 201
            token = latest_token(email)
            cm = api_client(app)
            api = cm.__enter__()
            contexts.append(cm)
            assert (
                api.post(
                    "/invitations/accept",
                    json={"token": token, "full_name": role, "password": STRONG_PASSWORD},
                ).status_code
                == 200
            )
            account = Account(email=email)
            login(api, account)
            enable_mfa(api, account)
            clients[role] = api
        yield clients
        for cm in contexts:
            cm.__exit__(None, None, None)


PERMISSION_ROUTES = [(r, v) for r, (k, v) in ROUTES if k == "permission"]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("route", "permission"), PERMISSION_ROUTES, ids=lambda x: getattr(x, "path", x))
def test_role_route_matrix(role_clients: dict[str, Api], role: str, route: APIRoute, permission: str) -> None:
    allowed = role in PERMISSIONS[permission]
    for method in route.methods or ():
        r = _call(role_clients[role], method, _url(route))
        refused = r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
        assert refused is not allowed, f"{role} {method} {route.path} -> {r.status_code} {r.text[:200]}"


NON_PUBLIC = [r for r, (k, _) in ROUTES if k != "public"]


@pytest.mark.parametrize("route", NON_PUBLIC, ids=lambda r: f"{sorted(r.methods)}{r.path}")
def test_unauthenticated_gets_401_everywhere(migrated_db: DbUrls, route: APIRoute) -> None:
    with api_client(make_app(migrated_db.app)) as anon:
        for method in route.methods or ():
            r = _call(anon, method, _url(route))
            assert r.status_code == 401, f"{method} {route.path} -> {r.status_code}"
