"""Master-data API (S3.1): CRUD, validation, optimistic concurrency, audit, unmapped queue; source-table
integrity triggers as the app role; source-line browsing (S3.4)."""

from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import Workspace, import_file, put
from tests.isolation.test_db_rls import as_app


def audit_actions(engine: Engine, tenant: str) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text("SELECT action FROM audit_log WHERE tenant_id = :t ORDER BY id"), {"t": tenant}
            ).scalars()
        )


def test_manufacturers_and_settings(ws: Workspace, admin_engine: Engine) -> None:
    volt = ws.manufacturers["VOLT"]
    dup = ws.api.post("/manufacturers", json={"name": "x", "code": "volt"})  # codes fold to upper case
    assert dup.status_code == 409
    bad = ws.api.post("/manufacturers", json={"name": "x", "code": "a b"})
    assert bad.status_code == 422
    r = ws.api.patch(f"/manufacturers/{volt}", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert ws.api.get("/manufacturers", params={"active": "false"}).json()["data"][0]["id"] == volt
    s = ws.api.get(f"/manufacturers/{volt}/settings")
    assert s.json()["variance_tolerance"] == "0.0100" and s.headers["ETag"] == '"1"'
    body = {"claim_window_days": 60, "claim_window_basis": "invoice_date", "variance_tolerance": "0.05"}
    assert put(ws.api, f"/manufacturers/{volt}/settings", body).status_code == 412  # If-Match required
    ok = put(ws.api, f"/manufacturers/{volt}/settings", body, {"If-Match": '"1"'})
    assert ok.status_code == 200 and ok.json()["revision"] == 2 and ok.json()["claim_window_days"] == 60
    assert put(ws.api, f"/manufacturers/{volt}/settings", body, {"If-Match": '"1"'}).status_code == 412
    floaty = put(ws.api, f"/manufacturers/{volt}/settings", {"variance_tolerance": 0.05}, {"If-Match": '"2"'})
    assert floaty.status_code == 422  # money is never a JSON number
    half = put(ws.api, f"/manufacturers/{volt}/settings", {"claim_window_days": 5}, {"If-Match": '"2"'})
    assert half.status_code == 422  # a window needs both days and basis
    actions = audit_actions(admin_engine, ws.tenant_id)
    assert {"manufacturer.created", "manufacturer.updated", "manufacturer.settings_updated"} <= set(actions)


def test_reason_code_mappings(ws: Workspace) -> None:
    volt = ws.manufacturers["VOLT"]
    body = {
        "mappings": [
            {"external_code": "E12", "external_text": "Price", "reason_code": "rejected_price_mismatch"}
        ]
    }
    r = put(ws.api, f"/manufacturers/{volt}/reason-code-mappings", body)
    assert r.status_code == 200 and r.json()["data"][0]["reason_code"] == "rejected_price_mismatch"
    unknown = put(
        ws.api,
        f"/manufacturers/{volt}/reason-code-mappings",
        {"mappings": [{"external_code": "X", "reason_code": "made_up"}]},
    )
    assert unknown.status_code == 422
    twice = {
        "mappings": [
            {"external_code": "A", "reason_code": "unknown"},
            {"external_code": "A", "reason_code": "unknown"},
        ]
    }
    assert put(ws.api, f"/manufacturers/{volt}/reason-code-mappings", twice).status_code == 422
    replaced = put(ws.api, f"/manufacturers/{volt}/reason-code-mappings", {"mappings": []})
    assert replaced.json()["data"] == []


def test_customers_aliases_parents(ws: Workspace) -> None:
    parent = ws.api.post("/customers", json={"name": "Parent Co (SYNTHETIC)", "code": "par1"}).json()
    assert parent["code"] == "PAR1"
    child = ws.api.post(
        "/customers", json={"name": "Child (SYNTHETIC)", "code": "KID1", "parent_customer_id": parent["id"]}
    )
    assert child.status_code == 201
    cycle = ws.api.patch(f"/customers/{parent['id']}", json={"parent_customer_id": child.json()["id"]})
    assert cycle.status_code == 422 and cycle.json()["error"]["details"][0]["code"] == "cycle"
    alias = ws.api.post(f"/customers/{parent['id']}/aliases", json={"alias": "parent co"})
    assert alias.status_code == 201 and alias.json()["alias"] == "PARENT CO"
    assert (
        ws.api.post(f"/customers/{child.json()['id']}/aliases", json={"alias": "Parent Co"}).status_code
        == 409
    )
    detail = ws.api.get(f"/customers/{parent['id']}").json()
    assert [a["alias"] for a in detail["aliases"]] == ["PARENT CO"]
    found = ws.api.get("/customers", params={"q": "child"}).json()["data"]
    assert [c["code"] for c in found] == ["KID1"]
    assert ws.api.get("/customers", params={"bogus": "1"}).status_code == 400


def test_products_uom_branches_and_pagination(ws: Workspace) -> None:
    volt = ws.manufacturers["VOLT"]
    p = ws.api.post(
        "/products",
        json={"sku": " 00999 ", "manufacturer_id": volt, "base_uom": "each", "list_cost": "1.234567"},
    )
    assert p.status_code == 201 and p.json()["sku"] == "00999" and p.json()["base_uom"] == "EACH"
    assert (
        ws.api.post(
            "/products",
            json={"sku": "X", "manufacturer_id": volt, "base_uom": "EA", "list_cost": "1.1234567"},
        ).status_code
        == 422
    )
    bad_mfr = ws.api.post(
        "/products",
        json={"sku": "Y", "manufacturer_id": "00000000-0000-7000-8000-000000000000", "base_uom": "EA"},
    )
    assert bad_mfr.status_code == 422
    pid = p.json()["id"]
    assert (
        ws.api.patch(f"/products/{pid}", json={"description": "Fuse (SYNTHETIC)"}).json()["description"]
        == "Fuse (SYNTHETIC)"
    )
    assert (
        ws.api.post(
            f"/products/{pid}/aliases", json={"alias": "123456789012", "alias_type": "upc"}
        ).status_code
        == 201
    )
    u = ws.api.post(
        "/uom-conversions", json={"product_id": pid, "from_uom": "CT", "to_uom": "EA", "factor": "12"}
    )
    assert u.status_code == 201 and u.json()["factor"] == "12.00000000"
    assert (
        ws.api.post("/uom-conversions", json={"from_uom": "EA", "to_uom": "EA", "factor": "1"}).status_code
        == 422
    )
    assert (
        ws.api.post("/uom-conversions", json={"from_uom": "BX", "to_uom": "EA", "factor": "0"}).status_code
        == 422
    )
    uid = u.json()["id"]
    assert put(ws.api, f"/uom-conversions/{uid}", {"factor": "24"}).json()["factor"] == "24.00000000"
    assert ws.api.delete(f"/uom-conversions/{uid}").status_code == 204
    assert ws.api.get(f"/uom-conversions?product_id={pid}").json()["data"] == []
    # keyset pagination over the 10 imported products + 1
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        page = ws.api.get("/products", params=params).json()
        seen += [x["id"] for x in page["data"]]
        if not page["page"]["has_more"]:
            break
        cursor = page["page"]["next_cursor"]
    assert len(seen) == len(set(seen)) == 11
    assert ws.api.get("/products", params={"cursor": "!!"}).status_code == 422
    assert [b["code"] for b in ws.api.get("/branches").json()["data"]] == ["BR01"]


def test_unmapped_ignore_and_auto_resolve(ws: Workspace) -> None:
    body = b"invoice_no,line_no,invoice_date,customer_code,sku,qty,uom,unit_price,ext_amount\nI1,1,2026-06-01,NEWCO,BRK-3P-100A,1,EA,1,1\nI2,1,2026-06-01,ALDER,ZZ-9,1,EA,1,1\n"
    from tests.integration.conftest import clean_file, map_suggested, start

    batch = map_suggested(ws, start(ws, clean_file(ws, "u.csv", body), "sales"))
    assert batch["counts"]["rejected"] == 2
    queue = {e["external_value"]: e for e in ws.api.get("/unmapped-entities").json()["data"]}
    assert queue["NEWCO"]["example_name"] is None and queue["ZZ-9"]["entity_type"] == "product"
    ign = ws.api.post(f"/unmapped-entities/{queue['ZZ-9']['id']}/resolve", json={"action": "ignore"})
    assert ign.json()["status"] == "ignored"
    assert (
        ws.api.post(
            f"/unmapped-entities/{queue['ZZ-9']['id']}/resolve", json={"action": "ignore"}
        ).status_code
        == 409
    )
    assert (
        ws.api.post(f"/unmapped-entities/{queue['NEWCO']['id']}/resolve", json={"action": "map"}).status_code
        == 422
    )
    ws.api.post(
        "/customers", json={"name": "New Co (SYNTHETIC)", "code": "NEWCO"}
    )  # creating it resolves the entry
    resolved = ws.api.get("/unmapped-entities", params={"status": "resolved"}).json()["data"]
    assert [e["external_value"] for e in resolved] == ["NEWCO"]


def test_source_rows_are_versioned_only(ws: Workspace, app_engine: Engine) -> None:
    import_file(ws, "sales/sales_hvac_rebate_scenario.csv", "sales")
    for sql in (
        "UPDATE sales_document_lines SET quantity = 1",
        "DELETE FROM sales_document_lines",
        "UPDATE sales_documents SET customer_id = customer_id, document_date = document_date + 1",
        "UPDATE sales_document_lines SET is_current = false",  # without superseded_by_id: CHECK
    ):
        with as_app(app_engine, ws.tenant_id) as conn, pytest.raises(DBAPIError) as err:
            conn.execute(text(sql))
        message = str(err.value.orig)
        assert any(
            m in message
            for m in ("versioned", "permission denied", "append-only", "ck_sales_document_lines_current")
        ), message
    with as_app(app_engine, ws.tenant_id) as conn, pytest.raises(DBAPIError) as err2:
        conn.execute(text("UPDATE reason_codes SET label = 'x'"))
    assert "permission denied" in str(err2.value.orig)


def test_sales_line_filters(ws: Workspace) -> None:
    import_file(ws, "sales/sales_scenarios_layoutA.csv", "sales")
    nwnd = ws.manufacturers["NWND"]
    import_file(ws, "sales/sales_hvac_rebate_scenario.csv", "sales")
    hv = ws.api.get("/sales-lines", params={"manufacturer_id": nwnd, "limit": 200}).json()["data"]
    assert len(hv) == 12
    june = ws.api.get(
        "/sales-lines", params={"manufacturer_id": nwnd, "date_from": "2026-06-01", "date_to": "2026-06-30"}
    ).json()["data"]
    assert sorted(x["document_number"] for x in june) == ["INV-50000", "INV-50001"]
    cns = ws.api.get("/sales-lines", params={"doc_type": "credit_note"}).json()["data"]
    assert {x["document_number"] for x in cns} == {"CN-5001", "CN-5002"} and all(
        x["quantity"].startswith("-") for x in cns
    )
    alder = ws.api.get("/customers", params={"q": "ALDER"}).json()["data"][0]["id"]
    by_customer = ws.api.get("/sales-lines", params={"customer_id": alder, "limit": 200}).json()["data"]
    assert len(by_customer) == 21  # 9 ALDER layout-A lines + 12 HVAC lines
    assert ws.api.get("/sales-lines", params={"date_from": "nope"}).status_code == 422
