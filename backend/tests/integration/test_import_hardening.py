"""Import hardening (PR #4 review: SEC-9..SEC-14): running-import cap, row cap, master-data audit,
parent cycles, PAN in headers, errors CSV export, mapping key limits. Real Postgres, SeaweedFS, ClamAV."""

import csv
import io
import time
import uuid

import pytest
from sqlalchemy import Engine, text

from rebate.modules.ingestion import service
from tests.integration.conftest import Workspace, clean_file, map_suggested, put, run_import, start
from tests.testdata import data


def _audit(engine: Engine, tenant: str, action: str) -> list[dict[str, object]]:
    with engine.connect() as conn:
        return [
            dict(r._mapping)
            for r in conn.execute(
                text(
                    "SELECT entity_id, actor_type, metadata FROM audit_log WHERE tenant_id = :t AND action = :a"
                ),
                {"t": tenant, "a": action},
            )
        ]


def _rows(rel: str) -> int:
    return len(list(csv.DictReader(io.StringIO(data(rel).decode("utf-8")))))


def test_fourth_running_import_is_refused(ws: Workspace) -> None:
    body = data("sales/sales_hvac_rebate_scenario.csv")
    fids = [clean_file(ws, f"r{i}.csv", body + b"\n" * i) for i in range(4)]  # distinct: no duplicate_file
    codes = []
    for fid in fids:  # no drain: every accepted import stays in "scanning"
        r = ws.api.post(
            "/imports",
            json={"file_id": fid, "import_type": "sales", "source_system": "ERP1"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        codes.append((r.status_code, r.json().get("error", {}).get("code")))
    assert codes[:3] == [(202, None)] * 3
    assert codes[3] == (409, "too_many_running_imports")


def test_file_over_the_row_cap_fails_the_batch(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "MAX_ROWS", 3)
    batch = start(ws, clean_file(ws, "big.csv", data("sales/sales_hvac_rebate_scenario.csv")), "sales")
    assert batch["status"] == "failed", batch
    errs = ws.api.get(f"/imports/{batch['id']}/errors").json()["data"]
    assert [e["code"] for e in errs] == ["too_many_rows"]


def test_master_data_imports_are_audited(ws: Workspace, admin_engine: Engine) -> None:
    # the ws fixture imported master/customers.csv, products.csv and uom_conversions.csv
    for action, rel in (
        ("customer.created", "master/customers.csv"),
        ("product.created", "master/products.csv"),
        ("uom_conversion.created", "master/uom_conversions.csv"),
    ):
        rows = _audit(admin_engine, ws.tenant_id, action)
        assert len(rows) == _rows(rel), action
        assert len({r["entity_id"] for r in rows}) == len(rows)


def test_parent_cycle_fails_the_batch_and_loads_nothing(ws: Workspace, admin_engine: Engine) -> None:
    head = "customer_code,customer_name,parent_customer_code\n"
    first = run_import(
        ws,
        clean_file(ws, "tree.csv", (head + "CYA,A (SYNTHETIC),\nCYB,B (SYNTHETIC),CYA\n").encode()),
        "customers",
    )
    assert first["status"] == "completed", first
    # CYB's parent is CYA in the database; making CYA a child of CYB closes the loop
    body = head + "CYA,A renamed (SYNTHETIC),CYB\nCYC,C (SYNTHETIC),\n"
    done = run_import(ws, clean_file(ws, "cycle.csv", body.encode()), "customers")
    assert done["status"] == "failed", done
    errs = ws.api.get(f"/imports/{done['id']}/errors").json()["data"]
    assert "customer_parent_cycle" in [e["code"] for e in errs]
    with admin_engine.connect() as conn:
        got: dict[str, str] = dict(
            conn.execute(
                text("SELECT code, name FROM customers WHERE tenant_id = :t AND code LIKE 'CY%'"),
                {"t": ws.tenant_id},
            ).all()
        )
    assert got == {"CYA": "A (SYNTHETIC)", "CYB": "B (SYNTHETIC)"}  # nothing of the refused chunk


def test_card_number_in_a_header_fails_the_batch(ws: Workspace, admin_engine: Engine) -> None:
    body = b"customer_code,4111 1111 1111 1111\nC1,x\n"
    batch = start(ws, clean_file(ws, "panhead.csv", body), "customers")
    assert batch["status"] == "failed" and batch["headers"] is None, batch
    errs = ws.api.get(f"/imports/{batch['id']}/errors").json()["data"]
    assert [e["code"] for e in errs] == ["card_number_in_header"]
    with admin_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT b::text FROM import_batches b WHERE id = :b"), {"b": batch["id"]}
        ).scalar_one()
    assert "4111" not in stored


def test_errors_csv_export_is_capped_and_audited(
    ws: Workspace, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = map_suggested(
        ws, start(ws, clean_file(ws, "e.csv", data("sales/sales_with_errors_layoutA.csv")), "sales")
    )
    full = ws.api.get(f"/imports/{batch['id']}/errors", params={"format": "csv"})
    assert full.status_code == 200 and full.headers["x-export-truncated"] == "false"
    n_errors = len(list(csv.reader(io.StringIO(full.text)))) - 1
    assert n_errors >= 3
    monkeypatch.setattr(service, "EXPORT_MAX_ROWS", 2)
    capped = ws.api.get(f"/imports/{batch['id']}/errors", params={"format": "csv"})
    assert capped.headers["x-export-truncated"] == "true"
    assert len(list(csv.reader(io.StringIO(capped.text)))) == 3  # header + 2
    events = _audit(admin_engine, ws.tenant_id, "import.errors_exported")
    got = sorted((e["metadata"]["rows"], e["metadata"]["truncated"]) for e in events)  # type: ignore[index]
    assert got == sorted([(n_errors, False), (2, True)])
    assert all(str(e["entity_id"]) == batch["id"] for e in events)


def test_mapping_key_longer_than_64_chars_is_rejected(ws: Workspace) -> None:
    batch = start(ws, clean_file(ws, "k.csv", data("sales/sales_hvac_rebate_scenario.csv")), "sales")
    r = put(ws.api, f"/imports/{batch['id']}/mapping", {"mapping": {"a" * 65: "qty"}})
    assert r.status_code == 422, r.text
    ok_shape = put(ws.api, f"/imports/{batch['id']}/mapping", {"mapping": {"a" * 64: "qty"}})
    assert ok_shape.status_code == 422  # passes the schema, refused as an unknown field
    assert "unknown_field" in {d["code"] for d in ok_shape.json()["error"]["details"]}


def test_template_with_a_card_number_header_is_rejected(ws: Workspace) -> None:
    body = {
        "name": "pan template",
        "import_type": "customers",
        "headers": ["code", "name", "4111 1111 1111 1111"],
        "mapping": {"customer_code": "code", "customer_name": "name"},
    }
    r = ws.api.post("/mapping-templates", json=body)
    assert r.status_code == 422, r.text
    assert ("headers[2]", "card_number") in {(d["field"], d["code"]) for d in r.json()["error"]["details"]}
    ok = ws.api.post("/mapping-templates", json={**body, "headers": ["code", "name", "x"]})
    assert ok.status_code == 201, ok.text
    update = {k: body[k] for k in ("headers", "mapping")}
    bad = put(ws.api, f"/mapping-templates/{ok.json()['id']}", update, {"If-Match": '"1"'})
    assert bad.status_code == 422 and "card_number" in {d["code"] for d in bad.json()["error"]["details"]}


@pytest.mark.performance  # about 45 s; run with -m performance (the default run covers cycles via CYA/CYB)
def test_deep_customer_chain_loads_in_bounded_time_and_cycles_still_fail(ws: Workspace) -> None:
    n = 20_000
    rows = ["customer_code,customer_name,parent_customer_code", "CH00000,Chain 0 (SYNTHETIC),"]
    rows += [f"CH{i:05d},Chain {i} (SYNTHETIC),CH{i - 1:05d}" for i in range(1, n)]
    t0 = time.perf_counter()
    done = run_import(ws, clean_file(ws, "chain.csv", ("\n".join(rows) + "\n").encode()), "customers")
    elapsed = time.perf_counter() - t0
    assert done["status"] == "completed" and done["counts"]["inserted"] == n, done
    assert elapsed < 60, f"{n}-customer chain took {elapsed:.0f}s"
    # the root becomes a child of the deepest existing customer: a cycle through existing rows only
    head = "customer_code,customer_name,parent_customer_code\n"
    body = f"{head}CH00000,Chain 0 (SYNTHETIC),CH{n - 1:05d}\n"
    cyc = run_import(ws, clean_file(ws, "chaincycle.csv", body.encode()), "customers")
    assert cyc["status"] == "failed", cyc
    codes = [e["code"] for e in ws.api.get(f"/imports/{cyc['id']}/errors").json()["data"]]
    assert "customer_parent_cycle" in codes
