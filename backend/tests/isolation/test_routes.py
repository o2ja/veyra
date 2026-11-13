"""Route-level isolation (kickoff report §6.2, R1-R6, R11). Two workspaces created through the API.

These tests guard the application layer (explicit tenant filters), so they must still pass with RLS
disabled; the negative control checks exactly that.
"""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import Engine, text

from rebate.api.app import MODULE_ROUTERS
from rebate.core.db import transaction
from rebate.modules.files import storage
from rebate.modules.reporting import service
from tests.conftest import DbUrls
from tests.integration.agreements_support import activate, payload
from tests.integration.conftest import Workspace, clean_file, import_file, put, run_import, seed_master, walk
from tests.security.flows import full_admin
from tests.support import Api, api_client, drain, fake_clock, latest_token, make_app, unique_email
from tests.testdata import data


@dataclass
class Side:
    api: Api
    tenant_id: str
    user_id: str
    invitation_id: str
    invitee: str
    file_id: str
    job_id: str
    s3: dict[str, str] = field(default_factory=dict)  # S3 path parameter -> one id of this tenant
    s4: dict[str, str] = field(default_factory=dict)  # S4 path parameter -> one id of this tenant
    s5: dict[str, str] = field(default_factory=dict)  # S5 path parameter -> one id of this tenant
    s6: dict[str, str] = field(default_factory=dict)  # S6 path parameter -> one id of this tenant
    s7: dict[str, str] = field(default_factory=dict)  # S7 path parameter -> one id of this tenant
    s9: dict[str, str] = field(default_factory=dict)  # S9 path parameter -> one id of this tenant
    s8: dict[str, str] = field(default_factory=dict)  # S8 path parameter -> one id of this tenant


@pytest.fixture(scope="module")
def sides(migrated_db: DbUrls, admin_engine: Engine) -> Iterator[tuple[Side, Side]]:
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
            side = Side(
                api, me["tenant"]["id"], me["user"]["id"], inv["id"], invitee, up.json()["id"], job["id"]
            )
            side.s3 = _seed_s3(Workspace(api, app, side.tenant_id, side.user_id, {}))
            side.s4 = _seed_s4(api)
            side.s5 = _seed_s5(app, api, side.s3)
            side.s6 = _seed_s6(api)
            side.s7 = _seed_s7(api, side.s3)
            side.s7["adjustment_id"] = _seed_adjustment(admin_engine, side)
            side.s9 = _seed_case(admin_engine, side)
            side.s8 = _seed_s8(app, admin_engine, side)
            out.append(side)
        yield out[0], out[1]


def _seed_s3(ws: Workspace) -> dict[str, str]:
    """Master data, a partial sales import (one line + one unmapped product), a template, purchases."""
    seed_master(ws)
    fid = clean_file(ws, "errors.csv", data("sales/sales_with_errors_layoutA.csv"))
    batch = run_import(ws, fid, "sales", accept_partial=True, template="iso-template")
    assert batch["status"] == "completed_with_errors", batch
    assert import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")["status"] == "completed"

    def first(path: str) -> str:
        rows = ws.api.get(path).json()["data"]
        assert rows, f"{path} must not be empty for the isolation fixture"
        return str(rows[0]["id"])

    return {
        "manufacturer_id": ws.manufacturers["VOLT"],
        "customer_id": first("/customers"),
        "product_id": first("/products"),
        "conversion_id": first("/uom-conversions"),
        "entry_id": first("/unmapped-entities"),
        "import_id": batch["id"],
        "template_id": first("/mapping-templates"),
        "line_id": first("/sales-lines"),
    }


def _seed_s4(api: Api) -> dict[str, str]:
    """Two approved agreements (four-eyes off: one admin is maker and approver here)."""
    etag = api.get("/settings").headers["ETag"]
    assert (
        api.patch("/settings", json={"four_eyes_required": False}, headers={"If-Match": etag}).status_code
        == 200
    )
    v1 = activate(api, api, payload("SPA-VOLT-2026-884"))
    v2 = activate(api, api, payload("REB-VOLT-2026"))
    return {"agreement_id": v1["agreement_id"], "draft_id": v1["draft_id"], "version_id": v1["id"],
            "other_version_id": v2["id"]}  # fmt: skip


def _seed_s5(app: Any, api: Api, s3: dict[str, str]) -> dict[str, str]:
    """A completed calculation run over the tenant's lines (through the real job), and one of its results."""
    r = api.post(
        "/calculation-runs",
        json={"date_from": "2000-01-01", "date_to": "2030-12-31"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 202, r.text
    drain(app)
    run = api.get(f"/calculation-runs/{r.json()['run']['id']}").json()
    assert run["status"] == "completed", run
    results = api.get(f"/calculation-runs/{run['id']}/results").json()["data"]
    assert results, "the isolation fixture needs a calculation result"
    return {"run_id": run["id"], "result_id": results[0]["id"]}


S5_PARAMS = ("run_id", "result_id")


def _seed_s6(api: Api) -> dict[str, str]:
    """AI on, one queued extraction of an agreement file (the job is never run here: no gateway configured)."""
    etag = api.get("/settings").headers["ETag"]
    assert (
        api.patch("/settings", json={"ai_processing_enabled": True}, headers={"If-Match": etag}).status_code
        == 200
    )
    csrf = api.csrf()
    up = api.client.post(
        "/api/v1/files",
        files={
            "file": (
                "agr.pdf",
                data("agreements/SYNTHETIC_spa_voltara_SPA-VOLT-2026-884.pdf"),
                "application/pdf",
            )
        },
        data={"kind": "agreement"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
    )
    assert up.status_code == 201, up.text
    drain()
    r = api.post("/document-extractions", json={"file_id": up.json()["id"]})
    assert r.status_code == 202, r.text
    return {"extraction_id": r.json()["id"]}


def _seed_s7(api: Api, s3: dict[str, str]) -> dict[str, str]:
    """An export template and a draft claim batch of the tenant (one eligible line stays unclaimed for the scan)."""
    tpl = api.post(
        "/claim-export-templates",
        json={
            "manufacturer_id": s3["manufacturer_id"],
            "name": "iso template",
            "format": "csv",
            "columns": [{"header": "Ref", "source": "claim_reference"}],
        },
    )
    assert tpl.status_code == 201, tpl.text
    eligible = api.get("/missed-claims", params={"manufacturer_id": s3["manufacturer_id"]}).json()["data"]
    assert eligible, "the isolation fixture needs an eligible line"
    batch = api.post(
        "/claim-batches",
        json={"manufacturer_id": s3["manufacturer_id"], "result_ids": [eligible[0]["id"]]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert batch.status_code == 201, batch.text
    # voided again: the batch row stays (history) and the line goes back to the scan, so lists of both are non-empty
    assert (
        api.post(
            f"/claim-batches/{batch.json()['id']}/void", json={"reason": "isolation fixture"}
        ).status_code
        == 200
    )
    return {
        "batch_id": batch.json()["id"],
        "export_template_id": tpl.json()["id"],
        "claimed_result_id": eligible[0]["id"],
    }


def _seed_adjustment(admin: Engine, side: Side) -> str:
    """A proposed adjustment against the claim line of the (voided) fixture batch. Adjustments come from a run that
    follows a submitted claim; the isolation world has none, so the owner role inserts the row (as for the DB-level
    isolation fixtures); the routes under test only read and move it."""
    adj = uuid.uuid4()
    with admin.begin() as conn:
        line = conn.execute(
            text(
                "SELECT l.id, b.manufacturer_id FROM claim_lines l JOIN claim_batches b ON b.tenant_id = l.tenant_id "
                "AND b.id = l.batch_id WHERE l.tenant_id = :t LIMIT 1"
            ),
            {"t": side.tenant_id},
        ).one()
        conn.execute(
            text(
                "INSERT INTO claim_adjustments (tenant_id, id, manufacturer_id, adjustment_type, claim_line_id, "
                "amount, reason) VALUES (:t, :a, :m, 'agreement_change', :l, -1, 'isolation fixture')"
            ),
            {"t": side.tenant_id, "a": adj, "m": line.manufacturer_id, "l": line.id},
        )
    return str(adj)


def _seed_case(admin: Engine, side: Side) -> dict[str, str]:
    """An open 'unpaid' case on the claim line of the fixture batch (cases come from a remittance finalisation, which
    the isolation world has none of, so the owner role inserts the row like for the adjustment above) and a deadline
    reminder for the tenant's admin."""
    case, note = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as conn:
        line = conn.execute(
            text(
                "SELECT l.id, b.manufacturer_id FROM claim_lines l JOIN claim_batches b ON b.tenant_id = l.tenant_id "
                "AND b.id = l.batch_id WHERE l.tenant_id = :t LIMIT 1"
            ),
            {"t": side.tenant_id},
        ).one()
        conn.execute(
            text(
                "INSERT INTO variance_cases (tenant_id, id, manufacturer_id, claim_line_id, case_type, reason_code, "
                "variance_amount) VALUES (:t, :c, :m, :l, 'unpaid', 'unpaid_no_remittance', 1)"
            ),
            {"t": side.tenant_id, "c": case, "m": line.manufacturer_id, "l": line.id},
        )
        conn.execute(
            text(
                "INSERT INTO notifications (tenant_id, id, user_id, kind, entity_type, entity_id, title, dedupe_key) "
                "VALUES (:t, :n, :u, 'deadline_reminder', 'variance_case', :c, 'isolation fixture', 'iso:14')"
            ),
            {"t": side.tenant_id, "n": note, "u": side.user_id, "c": case},
        )
    return {"case_id": str(case), "notification_id": str(note)}


def _seed_s8(app: Any, admin: Engine, side: Side) -> dict[str, str]:
    """An accepted remittance (CSV through the real ingest job, one line of 5.00) and a suggested match of its line to
    the fixture claim line (inserted by the owner role: a match to a claim needs a submitted batch the isolation world
    does not have; the routes under test only read and move it)."""
    api = side.api
    up = api.client.post(
        "/api/v1/files",
        files={
            "file": (
                "iso-remittance.csv",
                (f"invoice_no,paid{chr(10)}INV-ISO-{side.tenant_id},5.00{chr(10)}").encode(),
                "text/csv",
            )
        },
        data={"kind": "remittance"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": api.csrf()},
    )
    assert up.status_code == 201, up.text
    drain(app)
    r = api.post(
        "/remittances",
        json={
            "file_id": up.json()["id"],
            "manufacturer_id": side.s3["manufacturer_id"],
            "doc_type": "credit_memo",
            "channel": "credit_memo",
            "document_number": "ISO-1",
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 202, r.text
    drain(app)
    rid = r.json()["remittance"]["id"]
    assert api.get(f"/remittances/{rid}").json()["status"] == "accepted"
    (line,) = api.get(f"/remittances/{rid}/lines").json()["data"]
    match, claim_line = uuid.uuid4(), _claim_line(admin, side)
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reconciliation_matches (tenant_id, id, match_id, remittance_line_id, claim_line_id, "
                "allocated_amount, method, evidence, status) VALUES (:t, :m, :m, :r, :c, 5, 'manual', '{}', 'suggested')"
            ),
            {"t": side.tenant_id, "m": match, "r": line["id"], "c": claim_line},
        )
    return {"remittance_id": rid, "match_id": str(match), "remittance_line_id": line["id"],
            "claim_line_id": str(claim_line)}  # fmt: skip


def _claim_line(admin: Engine, side: Side) -> uuid.UUID:
    with admin.connect() as conn:
        return uuid.UUID(
            str(
                conn.execute(
                    text("SELECT id FROM claim_lines WHERE tenant_id = :t LIMIT 1"), {"t": side.tenant_id}
                ).scalar_one()
            )
        )


S8_PARAMS = (
    "remittance_id",
    "match_id",
    "remittance_line_id",
    "claim_line_id",
)  # the last two: S10 trace routes
S6_PARAMS = ("extraction_id",)
S9_PARAMS = ("case_id", "notification_id")
S7_PARAMS = ("batch_id", "export_template_id", "adjustment_id")
S4_PARAMS = ("agreement_id", "draft_id", "version_id", "other_version_id")
S3_PARAMS = ("manufacturer_id", "customer_id", "product_id", "conversion_id", "entry_id", "import_id",
             "template_id", "line_id")  # fmt: skip

# path parameter -> attribute of the *other* tenant used as the attack value
PARAM_SOURCES = {
    "user_id": "user_id",
    "invitation_id": "invitation_id",
    "file_id": "file_id",
    "job_id": "job_id",
    **{p: f"s3.{p}" for p in S3_PARAMS},
    **{p: f"s4.{p}" for p in S4_PARAMS},
    **{p: f"s5.{p}" for p in S5_PARAMS},
    **{p: f"s6.{p}" for p in S6_PARAMS},
    **{p: f"s7.{p}" for p in S7_PARAMS},
    **{p: f"s9.{p}" for p in S9_PARAMS},
    **{p: f"s8.{p}" for p in S8_PARAMS},
}


def _attack_value(side: Side, param: str) -> str:
    source = PARAM_SOURCES[param]
    if source.startswith("s3."):
        return side.s3[param]
    if source.startswith("s5."):
        return side.s5[param]
    if source.startswith("s7."):
        return side.s7[param]
    if source.startswith("s9."):
        return side.s9[param]
    if source.startswith("s8."):
        return side.s8[param]
    if source.startswith("s6."):
        return side.s6[param]
    return side.s4[param] if source.startswith("s4.") else str(getattr(side, source))


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
    if method == "PUT":  # If-Match "1" is the first revision/version, so only the id can make it fail
        return put(api, path, body, headers={"If-Match": '"1"'})
    if method == "POST":  # approve needs an Idempotency-Key, items:import an If-Match; others ignore them
        return api.post(path, json=body, headers={"Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'})
    return api.patch(path, json=body, headers={"If-Match": '"1"'})  # cases check the revision after the id


# A body that passes validation, so the response depends only on whether the id is visible.
BODIES: dict[str, Any] = {
    "/users/{user_id}": {"role": "viewer"},
    "/manufacturers/{manufacturer_id}/reason-code-mappings": {"mappings": []},
    "/customers/{customer_id}/aliases": {"alias": "ISO ALIAS"},
    "/products/{product_id}/aliases": {"alias": "ISO-ALIAS", "alias_type": "other"},
    "/uom-conversions/{conversion_id}": {"factor": "2"},
    "/unmapped-entities/{entry_id}/resolve": {"action": "ignore"},
    "/imports/{import_id}/mapping": {"mapping": {}},
    "/mapping-templates/{template_id}": {"headers": ["a"], "mapping": {}},
    "/agreements/{agreement_id}/cancel": {"effective_date": "2026-06-01", "reason": "x"},
    "/agreement-drafts/{draft_id}": {"payload": {}},
    "/agreement-drafts/{draft_id}/items:import": {
        "file_id": "00000000-0000-7000-8000-000000000000",
        "rate_format": "percent",
    },
    "/agreement-drafts/{draft_id}/submit": {"acknowledged_warnings": []},
    "/agreement-drafts/{draft_id}/return": {"comment": "x"},
    "/agreement-drafts/{draft_id}/reject": {"reason": "x"},
    "/claim-export-templates/{export_template_id}": {
        "name": "x",
        "format": "csv",
        "columns": [{"header": "Ref", "source": "claim_reference"}],
    },
    "/claim-batches/{batch_id}/submit": {"submitted_at": "2026-01-01", "external_reference": "x"},
    "/claim-batches/{batch_id}/void": {"reason": "x"},
    "/claim-batches/{batch_id}/export": {},
    "/cases/{case_id}": {"owner_user_id": None},
    "/cases/{case_id}/transition": {"to_status": "to_reclaim"},
    "/cases/{case_id}/notes": {"note": "x"},
    "/cases/{case_id}/attachments": {"file_id": "00000000-0000-7000-8000-000000000000"},
    "/cases/{case_id}/write-off": {"reason": "x"},
}


@pytest.mark.parametrize(("route", "method"), ID_ROUTES, ids=lambda x: getattr(x, "path", x))
def test_r1_foreign_ids_look_exactly_like_missing_ids(
    sides: tuple[Side, Side], route: APIRoute, method: str
) -> None:
    a, b = sides
    foreign = re.sub(r"\{([^}]+)\}", lambda m: _attack_value(b, m.group(1)), route.path)
    missing = re.sub(r"\{[^}]+\}", "00000000-0000-7000-8000-000000000000", route.path)
    body = BODIES.get(route.path, {}) if method in ("POST", "PATCH", "PUT") else None
    r_foreign = _call(a.api, method, foreign, body)
    r_missing = _call(a.api, method, missing, body)
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


S3_LISTS = (
    "/manufacturers", "/customers", "/products", "/branches", "/uom-conversions", "/unmapped-entities",
    "/imports", "/mapping-templates", "/sales-lines", "/purchase-lines", "/agreements", "/agreement-drafts",
    "/calculation-runs", "/rebate-accruals", "/claim-batches", "/claim-export-templates", "/missed-claims",
    "/claim-adjustments",
)  # fmt: skip


def _ws(side: Side) -> Workspace:
    return Workspace(side.api, None, side.tenant_id, side.user_id, {})  # type: ignore[arg-type]


@pytest.mark.parametrize("path", S3_LISTS)
def test_r2_s3_lists_never_contain_the_other_tenant(sides: tuple[Side, Side], path: str) -> None:
    a, b = sides
    own, other = set(walk(_ws(a), path, {"limit": 200})), set(walk(_ws(b), path, {"limit": 200}))
    assert own and other, f"{path}: both tenants need rows, otherwise this proves nothing"
    assert not own & other, f"{path}: A sees {len(own & other)} of B's rows"


def test_r2_extraction_list_and_usage_never_contain_the_other_tenant(sides: tuple[Side, Side]) -> None:
    a, b = sides
    own = {x["id"] for x in a.api.get("/document-extractions").json()["data"]}
    other = {x["id"] for x in b.api.get("/document-extractions").json()["data"]}
    assert own == {a.s6["extraction_id"]} and other == {b.s6["extraction_id"]}, (own, other)
    assert a.api.get("/document-extractions", params={"file_id": b.file_id}).json()["data"] == []
    assert a.api.get("/ai/usage").json()["daily"] == [] and b.api.get("/ai/usage").json()["daily"] == []


@pytest.mark.parametrize(
    ("path", "param", "source"),
    [
        ("/sales-lines", "customer_id", "customer_id"),
        ("/sales-lines", "product_id", "product_id"),
        ("/sales-lines", "import_batch_id", "import_id"),
        ("/purchase-lines", "manufacturer_id", "manufacturer_id"),
        ("/uom-conversions", "product_id", "product_id"),
    ],
)
def test_r2_filtering_by_a_foreign_id_returns_nothing(
    sides: tuple[Side, Side], path: str, param: str, source: str
) -> None:
    a, b = sides
    r = a.api.get(path, params={param: b.s3[source]})
    assert r.status_code == 200 and r.json()["data"] == [], f"{path}?{param}=<B id>: {r.text[:200]}"


def test_r3_s3_foreign_mutations_leave_the_other_tenant_unchanged(sides: tuple[Side, Side]) -> None:
    a, b = sides
    mfr, conv, entry = b.s3["manufacturer_id"], b.s3["conversion_id"], b.s3["entry_id"]
    before = b.api.get(f"/manufacturers/{mfr}").json()
    assert a.api.patch(f"/manufacturers/{mfr}", json={"name": "x"}).status_code == 404
    assert a.api.delete(f"/uom-conversions/{conv}").status_code == 404
    assert a.api.post(f"/unmapped-entities/{entry}/resolve", json={"action": "ignore"}).status_code == 404
    assert b.api.get(f"/manufacturers/{mfr}").json() == before
    assert conv in walk(_ws(b), "/uom-conversions", {})
    assert (
        next(e for e in b.api.get("/unmapped-entities").json()["data"] if e["id"] == entry)["status"]
        == "open"
    )


def test_r3_s4_foreign_mutations_leave_the_other_tenant_unchanged(sides: tuple[Side, Side]) -> None:
    a, b = sides
    agreement, version = b.s4["agreement_id"], b.s4["version_id"]
    before = b.api.get(f"/agreement-versions/{version}").json()
    cancel = {"effective_date": "2026-01-01", "reason": "x"}
    assert a.api.post(f"/agreements/{agreement}/cancel", json=cancel).status_code == 404
    assert (
        a.api.post("/agreement-drafts", json={"source": "manual", "amends_version_id": version}).status_code
        == 404
    )
    assert (
        a.api.post("/agreement-drafts", json={"source": "manual", "agreement_id": agreement}).status_code
        == 404
    )
    assert b.api.get(f"/agreement-versions/{version}").json() == before
    assert b.api.get(f"/agreements/{agreement}").json()["cancelled_at"] is None
    r = a.api.get(f"/agreement-versions/{a.s4['version_id']}/diff/{version}")
    assert r.status_code == 404, r.text  # one own and one foreign version


def test_s7_foreign_results_templates_and_batches_are_unreachable(sides: tuple[Side, Side]) -> None:
    a, b = sides
    foreign_result = b.s7["claimed_result_id"]
    mfr = a.s3["manufacturer_id"]
    missing = {"manufacturer_id": mfr, "result_ids": ["00000000-0000-7000-8000-000000000000"]}
    r_missing = a.api.post("/claim-batches", json=missing, headers={"Idempotency-Key": str(uuid.uuid4())})
    r = a.api.post(
        "/claim-batches",
        json={"manufacturer_id": mfr, "result_ids": [foreign_result]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == r_missing.status_code == 422
    assert (
        r.json()["error"]["details"][0]["code"]
        == r_missing.json()["error"]["details"][0]["code"]
        == "not_found"
    )
    # a foreign manufacturer id looks like an unknown one
    r = a.api.post(
        "/claim-batches",
        json={"manufacturer_id": b.s3["manufacturer_id"], "result_ids": [a.s7["claimed_result_id"]]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422
    # a foreign template cannot be used for an export, a foreign manufacturer cannot get a template
    own = a.api.post(
        "/claim-batches",
        json={"manufacturer_id": mfr, "result_ids": [a.s7["claimed_result_id"]]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()
    assert a.api.post(f"/claim-batches/{own['id']}/approve").status_code == 200
    r = a.api.post(f"/claim-batches/{own['id']}/export", json={"template_id": b.s7["export_template_id"]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert a.api.post(f"/claim-batches/{own['id']}/void", json={"reason": "cleanup"}).status_code == 200
    r = a.api.post(
        "/claim-export-templates",
        json={
            "manufacturer_id": b.s3["manufacturer_id"],
            "name": "x",
            "format": "csv",
            "columns": [{"header": "Ref", "source": "claim_reference"}],
        },
    )
    assert r.status_code == 422
    # the other tenant's batch and template are untouched
    assert b.api.get(f"/claim-batches/{b.s7['batch_id']}").json()["status"] == "void"
    assert b.api.get("/claim-export-templates").json()["data"][0]["revision"] == 1
    # filters by a foreign id match nothing
    for path, params in (
        ("/claim-batches", {"manufacturer_id": b.s3["manufacturer_id"]}),
        ("/missed-claims", {"manufacturer_id": b.s3["manufacturer_id"]}),
        ("/claim-export-templates", {"manufacturer_id": b.s3["manufacturer_id"]}),
    ):
        got = a.api.get(path, params=params)
        assert got.status_code == 200 and got.json()["data"] == [], path
    summary = a.api.get("/missed-claims/summary", params={"manufacturer_id": b.s3["manufacturer_id"]}).json()
    assert summary["totals"] == {"lines": 0, "amount": "0"} and summary["groups"] == []


def test_s8_foreign_remittances_lines_and_matches_are_unreachable(
    sides: tuple[Side, Side], admin_engine: Engine
) -> None:
    a, b = sides
    unknown = "00000000-0000-7000-8000-000000000000"
    # lists never contain the other tenant; filters by a foreign id match nothing
    assert b.s8["remittance_id"] not in {x["id"] for x in a.api.get("/remittances").json()["data"]}
    assert b.s8["match_id"] not in {x["id"] for x in a.api.get("/matches").json()["data"]}
    for path, params in (
        ("/remittances", {"manufacturer_id": b.s3["manufacturer_id"]}),
        ("/matches", {"remittance_id": b.s8["remittance_id"]}),
    ):
        got = a.api.get(path, params=params)
        assert got.status_code in (200, 404) and (got.status_code == 404 or got.json()["data"] == []), path
    # a manual match with a foreign remittance line or claim line looks like one with unknown ids
    own = {"remittance_line_id": a.s8["remittance_line_id"], "claim_line_id": a.s8["claim_line_id"]}
    for field_, foreign in (
        ("remittance_line_id", b.s8["remittance_line_id"]),
        ("claim_line_id", b.s8["claim_line_id"]),
    ):
        body, miss = own | {field_: foreign}, own | {field_: unknown}
        r = a.api.post("/matches", json={**body, "allocated_amount": "1.00"})
        m = a.api.post("/matches", json={**miss, "allocated_amount": "1.00"})
        assert r.status_code == m.status_code and r.status_code in (404, 422), r.text
        assert r.json()["error"]["code"] == m.json()["error"]["code"]
    # the other tenant's rows are untouched by all of the above
    with admin_engine.connect() as conn:
        st = conn.execute(
            text("SELECT status FROM reconciliation_matches WHERE id = :m"), {"m": b.s8["match_id"]}
        ).scalar_one()
        n = conn.execute(
            text("SELECT count(*) FROM reconciliation_matches WHERE tenant_id = :t"), {"t": b.tenant_id}
        ).scalar_one()
    assert (st, n) == ("suggested", 1)
    assert b.api.get(f"/remittances/{b.s8['remittance_id']}").json()["status"] == "accepted"


def test_s5_tracker_and_run_filters_never_show_the_other_tenant(sides: tuple[Side, Side]) -> None:
    a, b = sides
    own = {row["agreement_id"] for row in a.api.get("/rebate-tracker").json()["data"]}
    other = {row["agreement_id"] for row in b.api.get("/rebate-tracker").json()["data"]}
    assert own and other and not own & other
    # a foreign manufacturer/agreement filter on the caller's own run matches nothing
    path = f"/calculation-runs/{a.s5['run_id']}/results"
    for param, value in (("manufacturer_id", b.s3["manufacturer_id"]), ("agreement_id", next(iter(other)))):
        r = a.api.get(path, params={param: value})
        assert r.status_code == 200 and r.json()["data"] == [], (param, r.text[:200])
    # a foreign manufacturer cannot be put in a run scope; the error does not reveal whether it exists
    r = a.api.post(
        "/calculation-runs",
        json={
            "manufacturer_ids": [b.s3["manufacturer_id"]],
            "date_from": "2026-01-01",
            "date_to": "2026-12-31",
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    missing = a.api.post(
        "/calculation-runs",
        json={
            "manufacturer_ids": ["00000000-0000-7000-8000-000000000000"],
            "date_from": "2026-01-01",
            "date_to": "2026-12-31",
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == missing.status_code == 422
    assert r.json()["error"]["code"] == missing.json()["error"]["code"]
    assert r.json()["error"]["details"] == missing.json()["error"]["details"]


def test_s9_foreign_cases_and_notifications_are_unreachable(sides: tuple[Side, Side]) -> None:
    a, b = sides
    own = a.api.get("/cases", params={"limit": 200}).json()["data"]
    assert [c["id"] for c in own] == [a.s9["case_id"]], (
        "A sees exactly its own case (else this proves nothing)"
    )
    assert [n["id"] for n in a.api.get("/notifications").json()["data"]] == [a.s9["notification_id"]]
    # filters by a foreign id match nothing; the summary counts only the own case (variance 1)
    for params in ({"manufacturer_id": b.s3["manufacturer_id"]}, {"owner_user_id": b.user_id}):
        got = a.api.get("/cases", params=params)
        assert got.status_code == 200 and got.json()["data"] == [], params
    summary = a.api.get("/cases/summary").json()
    assert [(r["key"], r["count"], r["variance_amount"]) for r in summary["by_status"]] == [
        ("open", 1, "1.0000")
    ]
    # another tenant's file cannot be attached: it looks exactly like an unknown file
    foreign = a.api.post(f"/cases/{a.s9['case_id']}/attachments", json={"file_id": b.file_id})
    missing = a.api.post(
        f"/cases/{a.s9['case_id']}/attachments", json={"file_id": "00000000-0000-7000-8000-000000000000"}
    )
    assert foreign.status_code == missing.status_code and foreign.status_code in (404, 422)
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"]
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    # a foreign owner is not a member of this workspace
    r = a.api.patch(
        f"/cases/{a.s9['case_id']}", json={"owner_user_id": b.user_id}, headers={"If-Match": '"1"'}
    )
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "not_a_member"
    # nothing of B's changed through A's attempts
    assert b.api.get(f"/cases/{b.s9['case_id']}").json()["revision"] == 1
    assert b.api.get(f"/cases/{b.s9['case_id']}/events").json()["data"] == []
    assert [n["read_at"] for n in b.api.get("/notifications").json()["data"]] == [None]


def test_s10_dashboard_drilldown_and_trace_never_show_the_other_tenant(
    sides: tuple[Side, Side], app_engine: Engine
) -> None:
    """Each side has one unpaid case of 1.00 (no deadline): a leak would show 2.00 or the other's case id."""
    a, b = sides
    for side in (a, b):
        with transaction(app_engine, uuid.UUID(side.tenant_id)) as db:
            service.refresh(db, uuid.UUID(side.tenant_id))
    for own, other in ((a, b), (b, a)):
        body = own.api.get("/dashboard").json()
        assert [body["figures"][m] for m in ("short_paid", "at_risk")] == ["1.0000", "1.0000"]
        assert len(body["months"]) == 1
        drill = own.api.get("/dashboard/drilldown", params={"metric": "short_paid"}).json()
        assert [x["trace_id"] for x in drill["data"]] == [own.s9["case_id"]], "A sees exactly its own case"
        assert drill["total"] == "1.0000" and drill["consistent"] is True
        foreign = {"manufacturer_id": other.s3["manufacturer_id"]}
        assert set(own.api.get("/dashboard", params=foreign).json()["figures"].values()) == {"0"}
        assert (
            own.api.get("/dashboard/drilldown", params={"metric": "short_paid", **foreign}).json()["data"]
            == []
        )
        assert own.api.get(f"/trace/variance_case/{other.s9['case_id']}").status_code == 404
        assert own.api.get(f"/reports/claim-batches/{other.s7['batch_id']}/audit-pack").status_code == 404
        mine = own.api.get(f"/trace/variance_case/{own.s9['case_id']}").json()["chain"]
        assert {n["id"] for n in mine if n["kind"] == "claim_line"} <= {own.s8["claim_line_id"]}
