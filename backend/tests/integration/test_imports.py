"""S3.6 import pipeline against the SYNTHETIC files and ground truth (expected_import_results.json,
expected_changed_rows.json), through the real upload + ClamAV scan + job chain."""

import codecs
import csv
import io
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from rebate.modules.ingestion import claims_hook
from tests.integration.conftest import (
    XLSX,
    Workspace,
    clean_file,
    confirm,
    import_file,
    map_suggested,
    put,
    run_import,
    start,
    upload,
)
from tests.security import samples
from tests.support import drain
from tests.testdata import data, truth


def lines(engine: Engine, tenant: str, *, current: bool | None = True) -> list[Any]:
    sql = "SELECT * FROM sales_document_lines WHERE tenant_id = :t"
    if current is not None:
        sql += f" AND is_current = {'true' if current else 'false'}"
    with engine.connect() as conn:
        return list(
            conn.execute(text(sql + " ORDER BY document_number, line_number"), {"t": tenant}).mappings()
        )


def counts(batch: dict[str, Any]) -> dict[str, int]:
    return {k: batch["counts"][k] for k in ("inserted", "unchanged", "changed")}


def test_master_data_files_load(ws: Workspace, admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        t = {"t": ws.tenant_id}
        assert conn.execute(text("SELECT count(*) FROM customers WHERE tenant_id = :t"), t).scalar_one() == 7
        skus = set(conn.execute(text("SELECT sku FROM products WHERE tenant_id = :t"), t).scalars())
        assert len(skus) == 10 and "00418" in skus
        wire = conn.execute(
            text(
                "SELECT u.factor FROM uom_conversions u JOIN products p ON p.id = u.product_id "
                "WHERE u.tenant_id = :t AND p.sku = 'WIRE-12-2-RL' AND u.from_uom = 'RL' AND u.to_uom = 'FT'"
            ),
            t,
        ).scalar_one()
        assert wire == Decimal("500")
        cost = conn.execute(
            text("SELECT list_cost FROM products WHERE tenant_id = :t AND sku = '00418'"), t
        ).scalar_one()
        assert cost == Decimal("12.40")


def test_layout_a_imports_reimport_is_a_noop_and_lineage(ws: Workspace, admin_engine: Engine) -> None:
    fid = clean_file(ws, "sales_scenarios_layoutA.csv", data("sales/sales_scenarios_layoutA.csv"))
    first = run_import(ws, fid, "sales")
    assert first["status"] == "completed", first
    assert first["detected_encoding"] == "utf-8" and first["delimiter"] == ","
    assert counts(first) == {"inserted": 23, "unchanged": 0, "changed": 0}
    rows = lines(admin_engine, ws.tenant_id)
    assert len(rows) == 23
    # every line traceable to its file and row: row N of the CSV is the (N-1)th data record
    reader = list(csv.DictReader(io.StringIO(data("sales/sales_scenarios_layoutA.csv").decode())))
    for line in rows:
        src = reader[line["source_row_number"] - 2]
        assert (src["invoice_no"], int(src["line_no"])) == (line["document_number"], line["line_number"])
        assert str(line["import_batch_id"]) == first["id"]
    by_doc = {r["document_number"]: r for r in rows}
    cn = by_doc["CN-5001"]
    assert (
        cn["doc_type"] == "credit_note"
        and cn["quantity"] == Decimal("-5")
        and cn["extended_amount"] == Decimal("-290")
    )
    assert cn["original_line_id"] == by_doc["INV-10020"]["id"]  # FR-IM-08 link by (document, line)
    assert by_doc["CN-5002"]["original_line_id"] == by_doc["INV-10021"]["id"]
    assert by_doc["INV-10040"]["sku_raw"] == "00418" and by_doc["INV-10036"]["unit_cost"] is None
    assert (
        by_doc["INV-10001"]["manufacturer_id"] is not None
        and str(by_doc["INV-10001"]["manufacturer_id"]) == ws.manufacturers["VOLT"]
    )

    # lineage through the API
    line = ws.api.get(f"/sales-lines/{by_doc['INV-10001']['id']}").json()
    assert line["lineage"]["file_id"] == fid and line["lineage"]["source_row_number"] == 2
    assert (
        line["quantity"] == "100.000000"
        and line["unit_price"] == "88.000000"
        and line["extended_amount"] == "8800.0000"
    )

    # identical re-import of the same file (a new batch on the same file): a no-op
    gt = truth("expected_import_results.json")["sales_scenarios_layoutA.csv"]["reimport_same_file"]
    again = run_import(ws, fid, "sales")
    assert again["status"] == "completed" and counts(again) == gt
    assert len(lines(admin_engine, ws.tenant_id, current=None)) == 23
    # uploading the identical bytes again is refused at the file layer (layer 1)
    dup = upload(ws.api, "copy.csv", data("sales/sales_scenarios_layoutA.csv"))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_file"


def test_changed_rows_are_versioned_and_reported(ws: Workspace, admin_engine: Engine) -> None:
    import_file(ws, "sales/sales_scenarios_layoutA.csv", "sales")
    gt = truth("expected_changed_rows.json")["expected"]
    second = import_file(ws, "sales/sales_corrected_changed_rows.csv", "sales")
    assert second["status"] == "completed"
    assert (
        second["counts"]["changed"] == gt["changed"] == 2
        and second["counts"]["unchanged"] == gt["unchanged"] == 3
    )
    assert second["counts"]["inserted"] == 0
    report = ws.api.get(f"/imports/{second['id']}/changes").json()["data"]
    assert sorted(f"{c['document_number']}/{c['line_number']}" for c in report) == gt["changed_keys"]
    by_key = {c["document_number"]: c for c in report}
    assert by_key["INV-10001"]["changed_fields"]["unit_price"] == ["88.000000", "87.000000"]
    assert by_key["INV-10002"]["changed_fields"]["quantity"] == ["10.000000", "12.000000"]
    assert all(
        c["version"] == 2 and c["source_changed"] is False for c in report
    )  # no claims exist before S7
    allv = lines(admin_engine, ws.tenant_id, current=None)
    assert len(allv) == 25
    old = next(r for r in allv if r["document_number"] == "INV-10001" and not r["is_current"])
    new = next(r for r in allv if r["document_number"] == "INV-10001" and r["is_current"])
    assert (
        old["superseded_by_id"] == new["id"]
        and old["unit_price"] == Decimal("88")
        and new["unit_price"] == Decimal("87")
    )
    detail = ws.api.get(f"/sales-lines/{new['id']}").json()
    assert detail["previous_version_id"] == str(old["id"]) and detail["lineage"]["version"] == 2


def test_claimed_line_change_is_flagged_through_the_hook(
    ws: Workspace, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The S7 hook contract: superseded lines reported as claimed give the new version source_changed."""
    first = import_file(ws, "sales/sales_scenarios_layoutA.csv", "sales")
    claimed = {r["id"] for r in lines(admin_engine, ws.tenant_id) if r["document_number"] == "INV-10001"}
    seen: list[Any] = []

    def hook(db: Any, tenant_id: Any, ids: list[Any]) -> frozenset[Any]:
        seen.append(set(ids))
        return frozenset(i for i in ids if i in claimed)

    monkeypatch.setattr(claims_hook, "claimed_line_ids", hook)
    second = import_file(ws, "sales/sales_corrected_changed_rows.csv", "sales")
    assert first["counts"]["source_changed"] == 0 and second["counts"]["source_changed"] == 1
    flagged = [r["document_number"] for r in lines(admin_engine, ws.tenant_id) if r["source_changed"]]
    assert flagged == ["INV-10001"]
    assert len(seen[-1]) == 2  # both superseded lines were asked about


def test_hook_reports_no_claims_before_s7() -> None:
    assert claims_hook.claimed_line_ids(None, uuid.uuid4(), [uuid.uuid4()]) == frozenset()  # type: ignore[arg-type]


def test_error_file_partial_import_and_unmapped_queue(ws: Workspace, admin_engine: Engine) -> None:
    gt = truth("expected_import_results.json")["sales_with_errors_layoutA.csv"]
    fid = clean_file(ws, "errors.csv", data("sales/sales_with_errors_layoutA.csv"))
    batch = start(ws, fid, "sales")
    batch = map_suggested(ws, batch)
    c = batch["counts"]
    assert (c["rows_total"], c["valid"], c["duplicates"], c["rejected"]) == (8, 1, gt["duplicate_in_file"], 6)
    refused = ws.api.post(f"/imports/{batch['id']}/confirm", json={"accept_partial": False})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "import_has_errors"
    errs = ws.api.get(f"/imports/{batch['id']}/errors").json()["data"]
    got = {e["raw"]["invoice_no"]: e["code"] for e in errs if e["severity"] == "error"}
    assert got == {k: v.split(" ")[0] for k, v in gt["rejected_rows"].items()}
    assert next(e for e in errs if e["code"] == "invalid_date")["column_name"] == "invoice_date"
    done = confirm(ws, batch, accept_partial=True)
    assert (
        done["status"] == "completed_with_errors" and done["counts"]["inserted"] == gt["valid_inserted"] == 1
    )
    assert [r["document_number"] for r in lines(admin_engine, ws.tenant_id)] == ["INV-30001"]

    csv_export = ws.api.get(f"/imports/{batch['id']}/errors", params={"format": "csv"})
    assert csv_export.status_code == 200 and csv_export.headers["content-type"].startswith("text/csv")
    assert "unknown_product" in csv_export.text and "invoice_negative_quantity" in csv_export.text

    queue = ws.api.get("/unmapped-entities").json()["data"]
    entry = next(e for e in queue if e["external_value"] == "NOPE-123")
    assert entry["entity_type"] == "product" and entry["first_import_batch_id"] == batch["id"]
    product = ws.api.get("/products", params={"q": "BRK-3P-100A"}).json()["data"][0]
    r = ws.api.post(
        f"/unmapped-entities/{entry['id']}/resolve", json={"action": "map", "target_id": product["id"]}
    )
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert any(a["alias"] == "NOPE-123" for a in ws.api.get(f"/products/{product['id']}").json()["aliases"])
    # after resolving, importing the same file again loads the held row; everything else is unchanged
    again = run_import(ws, fid, "sales", accept_partial=True)
    assert (
        again["counts"]["inserted"] == 1
        and again["counts"]["unchanged"] == 1
        and again["counts"]["rejected"] == 5
    )
    assert "INV-30004" in [r["document_number"] for r in lines(admin_engine, ws.tenant_id)]


def test_layout_b_xlsx_us_formats_and_saved_template_reuse(ws: Workspace, admin_engine: Engine) -> None:
    first = import_file(ws, "sales/sales_layoutB_us_formats.xlsx", "sales", template="ERP layout B")
    assert (
        first["status"] == "completed" and first["detected_encoding"] == "xlsx" and first["header_row"] == 4
    )
    assert first["counts"]["inserted"] == 3 and first["mapping_template_version"] == 1
    rows = {r["document_number"]: r for r in lines(admin_engine, ws.tenant_id)}
    assert rows["INV-20001"]["quantity"] == Decimal("1000") and rows["INV-20001"][
        "extended_amount"
    ] == Decimal("88000.00")
    assert str(rows["INV-20003"]["invoice_date"]) == "2026-06-12"
    assert rows["INV-20002"]["sku_raw"] == "00418" and rows["INV-20003"]["source_row_number"] == 7
    # a file with the same header signature reuses the saved template: no mapping step
    template = ws.api.get("/mapping-templates", params={"import_type": "sales"}).json()["data"][0]
    assert template["name"] == "ERP layout B" and template["version"] == 1
    other = io.BytesIO()
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    wb = load_workbook(io.BytesIO(data("sales/sales_layoutB_us_formats.xlsx")))
    wb.active["A5"] = "INV-29999"
    wb.save(other)
    fid = clean_file(ws, "layoutB-2.xlsx", other.getvalue(), XLSX)
    batch = start(ws, fid, "sales")
    assert batch["needs_mapping"] is False and batch["mapping_template_id"] == template["id"]
    assert confirm(ws, batch)["counts"]["inserted"] == 1  # only the renamed invoice is new

    # versioning: PUT needs the current version (If-Match)
    body = {"headers": batch["headers"], "mapping": template["mapping"], "options": {"date_order": "MDY"}}
    stale = put(ws.api, f"/mapping-templates/{template['id']}", body, {"If-Match": '"7"'})
    assert stale.status_code == 412
    ok = put(ws.api, f"/mapping-templates/{template['id']}", body, {"If-Match": '"1"'})
    assert ok.status_code == 200 and ok.json()["version"] == 2


def test_purchases_hvac_and_volume(ws: Workspace, admin_engine: Engine) -> None:
    p = import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")
    assert p["status"] == "completed" and p["counts"]["inserted"] == 10
    with admin_engine.connect() as conn:
        total = conn.execute(
            text(
                "SELECT sum(extended_amount) FROM purchase_document_lines WHERE tenant_id = :t AND is_current"
            ),
            {"t": ws.tenant_id},
        ).scalar_one()
        vc = conn.execute(
            text(
                "SELECT quantity FROM purchase_document_lines WHERE tenant_id = :t AND doc_type = 'vendor_credit'"
            ),
            {"t": ws.tenant_id},
        ).scalar_one()
    # TEST_DATA_SPEC §2.4: receipts 990,000 to 2026-08-15 + 20,000 on 08-31 - 20,000 vendor credit
    assert total == Decimal("990000") and vc == Decimal("-200")
    listed = ws.api.get("/purchase-lines", params={"doc_type": "vendor_credit"}).json()["data"]
    assert len(listed) == 1 and listed[0]["extended_amount"] == "-20000.0000"

    h = import_file(ws, "sales/sales_hvac_rebate_scenario.csv", "sales")
    assert h["counts"]["inserted"] == 12
    v = import_file(ws, "sales/sales_volume_2000.csv", "sales")
    assert v["status"] == "completed" and v["counts"]["inserted"] == 2000


def _sales_csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def _state(engine: Engine, tenant: str) -> set[tuple[Any, ...]]:
    return {
        (
            r["document_number"],
            r["line_number"],
            r["row_hash"],
            r["quantity"],
            r["original_line_id"] is not None,
        )
        for r in lines(engine, tenant)
    }


def test_order_independence(app: Any, clk: Any, admin_engine: Engine) -> None:
    """Shuffled rows (credit notes before their invoices) and swapped file order give the same lines."""
    from tests.integration.conftest import new_workspace
    from tests.support import api_client

    original = list(csv.DictReader(io.StringIO(data("sales/sales_scenarios_layoutA.csv").decode())))
    states = []
    with api_client(app) as a, api_client(app) as b:
        wa, wb = new_workspace(app, a), new_workspace(app, b)
        run_import(wa, clean_file(wa, "a.csv", data("sales/sales_scenarios_layoutA.csv")), "sales")
        run_import(wa, clean_file(wa, "h.csv", data("sales/sales_hvac_rebate_scenario.csv")), "sales")
        run_import(wb, clean_file(wb, "h.csv", data("sales/sales_hvac_rebate_scenario.csv")), "sales")
        run_import(wb, clean_file(wb, "rev.csv", _sales_csv(list(reversed(original)))), "sales")
        states = [_state(admin_engine, wa.tenant_id), _state(admin_engine, wb.tenant_id)]
    assert states[0] == states[1] and len(states[0]) == 35


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-8", b""),
        ("utf-8", codecs.BOM_UTF8),
        ("cp1252", b""),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
    ],
)
def test_encodings_through_upload(ws: Workspace, admin_engine: Engine, encoding: str, bom: bytes) -> None:
    body = bom + "customer_code,customer_name\nCAFE,Café Zoë (SYNTHETIC)\n".encode(encoding)
    done = run_import(ws, clean_file(ws, "c.csv", body), "customers")
    expected = {b"": encoding, codecs.BOM_UTF8: "utf-8-sig"}.get(bom, encoding)
    assert done["status"] == "completed" and done["detected_encoding"] == expected
    with admin_engine.connect() as conn:
        name = conn.execute(
            text("SELECT name FROM customers WHERE tenant_id = :t AND code = 'CAFE'"), {"t": ws.tenant_id}
        ).scalar_one()
    assert name == "Café Zoë (SYNTHETIC)"


def test_undecodable_upload_is_rejected_with_a_clear_message(ws: Workspace) -> None:
    # text-looking bytes that are neither UTF-8 nor Windows-1252 (0x81 and 0x8D are undefined there)
    r = upload(ws.api, "bad.csv", b"a,b\nCaf\x81 x,1\nZo\x8d,2\n")
    assert r.status_code == 422
    detail = r.json()["error"]["details"][0]
    assert detail["code"] == "encoding_unsupported" and "UTF-8" in detail["message"]


def test_pan_guard_never_stores_card_numbers(ws: Workspace, admin_engine: Engine) -> None:
    body = b"customer_code,customer_name,external_id\nPAN1,Card Holder (SYNTHETIC),4111 1111 1111 1111\nOK1,Fine (SYNTHETIC),X1\n"
    batch = map_suggested(ws, start(ws, clean_file(ws, "pan.csv", body), "customers"))
    errs = ws.api.get(f"/imports/{batch['id']}/errors").json()["data"]
    assert [e["code"] for e in errs] == ["possible_card_number"]
    with admin_engine.connect() as conn:
        stored = " ".join(
            str(x)
            for x in conn.execute(
                text("SELECT raw::text FROM import_staged_rows WHERE import_batch_id = :b UNION ALL "
                     "SELECT raw::text FROM import_row_errors WHERE import_batch_id = :b"),
                {"b": batch["id"]},
            ).scalars()
        )  # fmt: skip
    assert "4111" not in stored and "card number removed" in stored


def test_infected_file_is_rejected_by_the_chain(ws: Workspace, clk: Any) -> None:
    r = upload(ws.api, "eicar.csv", samples.eicar())
    assert r.status_code == 201
    started = ws.api.post(
        "/imports",
        json={"file_id": r.json()["id"], "import_type": "sales", "source_system": "ERP1"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert started.status_code == 202 and started.json()["import"]["status"] == "scanning"
    drain(ws.app)  # scan (infected); a parse that ran first polls again 5 s later
    clk.advance(6)
    drain(ws.app)
    batch = ws.api.get(f"/imports/{started.json()['import']['id']}").json()
    assert batch["status"] == "rejected" and "malware" in batch["error_summary"]
    again = ws.api.post(
        "/imports",
        json={"file_id": r.json()["id"], "import_type": "sales"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"


def test_idempotency_key_replays_and_conflicts(ws: Workspace) -> None:
    fid = clean_file(ws, "idem.csv", data("sales/sales_hvac_rebate_scenario.csv"))
    key = str(uuid.uuid4())
    body = {"file_id": fid, "import_type": "sales", "source_system": "ERP1"}
    first = ws.api.post("/imports", json=body, headers={"Idempotency-Key": key})
    replay = ws.api.post("/imports", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == replay.status_code == 202
    assert replay.json()["import"]["id"] == first.json()["import"]["id"]
    other = ws.api.post("/imports", json={**body, "source_system": "ERP2"}, headers={"Idempotency-Key": key})
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    missing = ws.api.post("/imports", json=body)
    assert missing.status_code == 400
    assert len(ws.api.get("/imports", params={"import_type": "sales"}).json()["data"]) == 1


def test_suggest_mapping_says_ai_is_not_enabled(ws: Workspace) -> None:
    batch = start(ws, clean_file(ws, "s.csv", data("sales/sales_hvac_rebate_scenario.csv")), "sales")
    r = ws.api.post(f"/imports/{batch['id']}/suggest-mapping")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_enabled"


def test_mapping_validation_and_state_rules(ws: Workspace) -> None:
    batch = start(ws, clean_file(ws, "m.csv", data("sales/sales_hvac_rebate_scenario.csv")), "sales")
    assert batch["needs_mapping"] and batch["counts"]["rows_total"] == 12
    bad = put(ws.api, f"/imports/{batch['id']}/mapping", {"mapping": {"quantity": "nope"}})
    assert bad.status_code == 422
    fields = {d["field"] for d in bad.json()["error"]["details"]}
    assert "mapping.quantity" in fields and "mapping.document_number" in fields
    early = ws.api.post(f"/imports/{batch['id']}/confirm", json={})
    assert early.status_code == 409  # nothing to confirm before a mapping exists
    preview = ws.api.get(f"/imports/{batch['id']}/preview").json()
    assert preview["suggested_mapping"]["quantity"] == "qty" and len(preview["rows"]) == 12
    assert preview["import"]["id"] == batch["id"]


def test_unreadable_file_fails_the_batch_visibly(ws: Workspace) -> None:
    batch = start(ws, clean_file(ws, "bad.csv", b'a,b\n"open,1\n'), "sales")
    assert batch["status"] == "failed" and "malformed" in batch["error_summary"]
    errs = ws.api.get(f"/imports/{batch['id']}/errors").json()["data"]
    assert errs[0]["code"] == "malformed_csv" and errs[0]["row_number"] is None


def test_error_csv_export_neutralises_formulas() -> None:
    # The export currently carries no raw cell values (message/column are system text), so the guard is
    # defence in depth: prove it on the real formatter, for every trigger character in SECURITY.md section 4.
    from rebate.modules.ingestion.router import _csv_lines

    triggers = ["=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(1)", "\t=1", "\r=1"]
    out = "".join(_csv_lines([(i, v, "error", "x", v) for i, v in enumerate(triggers)]))
    cells = [c for row in csv.reader(io.StringIO(out, newline="")) for c in row]
    for v in triggers:
        assert "'" + v in cells and v not in cells
    assert next(csv.reader(io.StringIO(out, newline="")))[1] == "column"  # header row is untouched
