"""S3 integration harness: real API app, Postgres, SeaweedFS and ClamAV. Files go through the real
upload -> malware scan -> import job chain; jobs run in-process via ``drain``."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI

from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.security.flows import full_admin
from tests.support import Api, FakeClock, api_client, drain, fake_clock, make_app, make_settings, unique_email
from tests.testdata import data, truth

CSV = "text/csv"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="session", autouse=True)
def bucket(migrated_db: DbUrls) -> None:
    storage.ensure_bucket(make_settings(migrated_db.app))


@pytest.fixture
def clk() -> Iterator[FakeClock]:
    with fake_clock() as fc:
        yield fc


@pytest.fixture
def app(migrated_db: DbUrls) -> FastAPI:
    return make_app(migrated_db.app)


@dataclass
class Workspace:
    api: Api
    app: FastAPI
    tenant_id: str
    user_id: str
    manufacturers: dict[str, str]


def upload(api: Api, name: str, body: bytes, mime: str = CSV) -> Any:
    return api.client.post(
        "/api/v1/files",
        files={"file": (name, body, mime)},
        data={"kind": "import"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": api.csrf()},
    )


def put(api: Api, path: str, json: Any, headers: dict[str, str] | None = None) -> Any:
    return api.client.put(
        f"/api/v1{path}",
        json=json,
        headers={"Origin": "https://testserver", "X-CSRF-Token": api.csrf(), **(headers or {})},
    )


def clean_file(ws: Workspace, name: str, body: bytes, mime: str = CSV) -> str:
    r = upload(ws.api, name, body, mime)
    assert r.status_code == 201, r.text
    drain(ws.app)
    f = ws.api.get(f"/files/{r.json()['id']}").json()
    assert f["scan_status"] == "clean", f
    return str(f["id"])


def start(
    ws: Workspace, file_id: str, import_type: str, source_system: str = "ERP1", **extra: Any
) -> dict[str, Any]:
    r = ws.api.post(
        "/imports",
        json={"file_id": file_id, "import_type": import_type, "source_system": source_system, **extra},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 202, r.text
    drain(ws.app)
    return dict(ws.api.get(f"/imports/{r.json()['import']['id']}").json())


def map_suggested(
    ws: Workspace, batch: dict[str, Any], template: str | None = None, **options: Any
) -> dict[str, Any]:
    preview = ws.api.get(f"/imports/{batch['id']}/preview").json()
    mapping = {k: v for k, v in preview["suggested_mapping"].items() if v is not None}
    body: dict[str, Any] = {"mapping": mapping, "options": options}
    if template:
        body["save_as_template"] = {"name": template}
    r = put(ws.api, f"/imports/{batch['id']}/mapping", body)
    assert r.status_code == 202, r.text
    drain(ws.app)
    return dict(ws.api.get(f"/imports/{batch['id']}").json())


def confirm(ws: Workspace, batch: dict[str, Any], accept_partial: bool = False) -> dict[str, Any]:
    r = ws.api.post(f"/imports/{batch['id']}/confirm", json={"accept_partial": accept_partial})
    assert r.status_code == 202, r.text
    drain(ws.app, limit=5000)
    return dict(ws.api.get(f"/imports/{batch['id']}").json())


def run_import(
    ws: Workspace,
    file_id: str,
    import_type: str,
    *,
    source_system: str = "ERP1",
    accept_partial: bool = False,
    template: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    batch = start(ws, file_id, import_type, source_system)
    assert batch["status"] == "awaiting_confirmation", batch
    if batch["needs_mapping"]:
        batch = map_suggested(ws, batch, template, **options)
        assert batch["status"] == "awaiting_confirmation", batch
    return confirm(ws, batch, accept_partial)


def import_file(ws: Workspace, rel: str, import_type: str, **kw: Any) -> dict[str, Any]:
    mime = XLSX if rel.endswith(".xlsx") else CSV
    return run_import(ws, clean_file(ws, rel.rsplit("/", 1)[-1], data(rel), mime), import_type, **kw)


def new_workspace(app: FastAPI, api: Api, *, master: bool = True) -> Workspace:
    full_admin(api, unique_email("s3"), workspace="Midway Electrical Supply (SYNTHETIC)")
    me = api.get("/auth/me").json()
    ws = Workspace(api, app, me["tenant"]["id"], me["user"]["id"], {})
    for m in truth("master_manufacturers.json"):
        r = api.post("/manufacturers", json={"name": m["name"], "code": m["code"]})
        assert r.status_code == 201, r.text
        ws.manufacturers[m["code"]] = r.json()["id"]
    if master:
        api.post("/branches", json={"name": "Branch 01 (SYNTHETIC)", "code": "BR01"})
        for rel, kind in (
            ("master/customers.csv", "customers"),
            ("master/products.csv", "products"),
            ("master/uom_conversions.csv", "uom"),
        ):
            done = import_file(ws, rel, kind)
            assert done["status"] == "completed", done
    return ws


@pytest.fixture
def ws(app: FastAPI, clk: FakeClock) -> Iterator[Workspace]:
    with api_client(app) as api:
        yield new_workspace(app, api)
