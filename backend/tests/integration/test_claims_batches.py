"""S7 claims core end to end: scan -> batch -> approve -> export -> submit, on the SYNTHETIC scenario month, with real
imports, agreements, calculation run, job queue, object storage and Postgres. Expected values come from
``testdata/generated/ground_truth`` (``expected_calculation.json``, ``claim_batches_fixture.json``) and hand
derivation, never from the output of the code under test.

Line pool (11 eligible lines of the scenario month): FIX7 = the seven lines of the ground-truth batch
CB-VOLT-2026-06 (consumed by the acceptance flow, last); the other four serve the remaining tests, each test
releasing what it claimed (void) so the next one finds the pool intact."""

import csv
import io
import threading
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from openpyxl import load_workbook  # type: ignore[import-untyped]
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.integration.agreements_support import member
from tests.integration.calc_support import (
    activate_all,
    all_settings,
    import_sales_part,
    open_workspace,
    results,
    run,
)
from tests.integration.conftest import Workspace
from tests.support import Api, FakeClock, drain, fake_clock, make_app
from tests.testdata import truth

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
SPA_NAMES = ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901", "SPA-BRIT-2026-031", "SPA-COPR-2026-107")
WINDOW = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
CALC = {f"{s['invoice_no']}/{s['line_no']}": s for s in truth("expected_calculation.json")["scenarios"]}
BATCH_FIXTURE = truth("claim_batches_fixture.json")
FIX7 = [f"{x['invoice_no']}/{x['line_no']}" for x in BATCH_FIXTURE["lines"]]
ELIGIBLE = {k: s for k, s in CALC.items() if s["status"] == "eligible"}
POOL4 = sorted(set(ELIGIBLE) - set(FIX7))  # SC-03, SC-08, SC-12, SC-14
EXPECTED_AMOUNT = {k: Decimal(s["claim_amount"]) for k, s in ELIGIBLE.items()}


@dataclass
class Env:
    app: FastAPI
    stack: ExitStack
    clock: FakeClock


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


@dataclass
class Scenario:
    ws: Workspace
    versions: dict[str, dict[str, Any]]
    mfr: dict[str, str]  # sales line key -> manufacturer id (from the run's results)
    approver: Api
    analyst: Api
    viewer: Api


@pytest.fixture(scope="module")
def sc(env: Env, admin_engine: Engine) -> Scenario:
    """The scenario month, calculated once, before anything is claimed."""
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    versions = activate_all(ws, SPA_NAMES)
    import_sales_part(ws, late=False)
    r = run(ws, **WINDOW)
    assert r["status"] == "completed", r
    res = results(ws, r["id"])
    mfr = {k: x["manufacturer_id"] for k, x in res.items() if x["status"] == "eligible"}
    return Scenario(
        ws,
        versions,
        mfr,
        member(env.stack, env.app, ws.api, "approver"),
        member(env.stack, env.app, ws.api, "analyst"),
        member(env.stack, env.app, ws.api, "viewer"),
    )


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def pool(api: Api) -> dict[str, dict[str, Any]]:
    """Unclaimed eligible lines by ``<document>/<line>``, from GET /missed-claims."""
    out: dict[str, dict[str, Any]] = {}
    cursor = None
    while True:
        r = api.get("/missed-claims", params={"limit": 200, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        body = r.json()
        out |= {f"{x['document_number']}/{x['line_number']}": x for x in body["data"]}
        if not body["page"]["has_more"]:
            return out
        cursor = body["page"]["next_cursor"]


def make(api: Api, keys: list[str], manufacturer: str, **kw: Any) -> Any:
    ids = [pool(api)[k]["id"] for k in keys]
    return api.post(
        "/claim-batches",
        json={"manufacturer_id": manufacturer, "result_ids": ids},
        headers=kw.get("headers") or key(),
    )


def one(sc: Scenario, k: str, api: Api | None = None) -> dict[str, Any]:
    """A draft batch of the single line ``k`` (its own manufacturer)."""
    r = make(api or sc.ws.api, [k], sc.mfr[k])
    assert r.status_code == 201, r.text
    return dict(r.json())


def release(sc: Scenario, batch: dict[str, Any]) -> None:
    r = sc.approver.post(f"/claim-batches/{batch['id']}/void", json={"reason": "test cleanup"})
    assert r.status_code == 200, r.text


def lines(api: Api, batch_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        r = api.get(
            f"/claim-batches/{batch_id}/lines",
            params={"limit": 200, **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200, r.text
        out += r.json()["data"]
        if not r.json()["page"]["has_more"]:
            return out
        cursor = r.json()["page"]["next_cursor"]


def file_bytes(env: Env, engine: Engine, file_id: str) -> bytes:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT storage_key, kind, scan_status FROM file_objects WHERE id = :i"), {"i": file_id}
        ).one()
    assert row.kind == "export" and row.scan_status == "clean"
    return b"".join(storage.read_chunks(env.app.state.settings, row.storage_key))


def audit_actions(api: Api, entity_id: str) -> list[str]:
    r = api.get("/audit-log", params={"entity_id": entity_id, "limit": 200})
    assert r.status_code == 200, r.text
    return [e["action"] for e in r.json()["data"]]


# ---- ground truth sanity of the test data itself ----------------------------------------------------------


def test_the_ground_truth_batch_is_the_seven_eligible_lines_summing_to_10410_before_the_adjustment() -> None:
    assert set(FIX7) <= set(ELIGIBLE) and len(POOL4) == 4
    claimed = sum((EXPECTED_AMOUNT[k] for k in FIX7), Decimal(0))
    assert claimed == Decimal("10410.00")  # hand sum 2200+3120+2080+250+1000+1100+660
    adjustments = sum((Decimal(a["amount"]) for a in BATCH_FIXTURE["adjustments"]), Decimal(0))
    assert claimed + adjustments == Decimal(BATCH_FIXTURE["batch_total"]) == Decimal("10360.00")


# ---- S7.2 missed claims -----------------------------------------------------------------------------------


def test_missed_claims_list_is_exactly_the_eligible_scenarios_with_their_amounts(sc: Scenario) -> None:
    found = pool(sc.ws.api)
    assert set(found) == set(ELIGIBLE)
    for k, row in found.items():
        assert Decimal(row["claim_amount"]) == EXPECTED_AMOUNT[k], k
        assert row["manufacturer_id"] == sc.mfr[k]
    assert sc.viewer.get("/missed-claims").status_code == 200  # data.read


def test_missed_claims_summary_totals_and_exceptions(sc: Scenario) -> None:
    body = sc.ws.api.get("/missed-claims/summary").json()
    expected = sum(EXPECTED_AMOUNT.values(), Decimal(0))
    assert expected == Decimal("11430.50")  # hand sum of the eleven eligible scenarios
    assert body["totals"]["lines"] == len(ELIGIBLE) and Decimal(body["totals"]["amount"]) == expected
    assert sum(Decimal(g["amount"]) for g in body["groups"]) == expected
    assert sum(g["lines"] for g in body["groups"]) == len(ELIGIBLE)
    by_code = {e["code"]: e["lines"] for e in body["exceptions"]}
    truth_codes: dict[str, int] = {}
    for s in CALC.values():
        if s["status"] == "exception":
            truth_codes[s["exception_code"]] = truth_codes.get(s["exception_code"], 0) + 1
    for code, n in truth_codes.items():
        assert by_code.get(code, 0) == n, code  # every documented scenario exception, exactly once
    lost = sum(
        (
            Decimal(s.get("claim_amount", "0"))
            for s in CALC.values()
            if s.get("exception_code") == "outside_claim_window"
        ),
        Decimal(0),
    )
    assert lost > 0 and Decimal(body["lost_to_expiry"]["amount"]) == lost
    one_mfr = sc.ws.api.get("/missed-claims/summary", params={"manufacturer_id": sc.mfr[FIX7[0]]}).json()
    assert 0 < one_mfr["totals"]["lines"] <= len(ELIGIBLE)


def test_missed_claims_filters_and_unknown_parameters(sc: Scenario) -> None:
    api = sc.ws.api
    k = POOL4[0]
    only = api.get("/missed-claims", params={"manufacturer_id": sc.mfr[k]}).json()["data"]
    assert only and {x["manufacturer_id"] for x in only} == {sc.mfr[k]}
    assert api.get("/missed-claims", params={"invoice_from": "2030-01-01"}).json()["data"] == []
    assert api.get("/missed-claims", params={"nope": "1"}).status_code == 400
    assert api.get("/missed-claims", params={"format": "pdf"}).status_code == 422


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_missed_claims_report_job_produces_the_file(
    sc: Scenario, env: Env, admin_engine: Engine, fmt: str
) -> None:
    r = sc.ws.api.get("/missed-claims", params={"format": fmt})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job"]["result_url"] == f"/api/v1/files/{body['file_id']}"
    assert sc.ws.api.get(f"/files/{body['file_id']}").status_code == 404  # not produced yet
    drain(env.app)
    meta = sc.ws.api.get(f"/files/{body['file_id']}")
    assert (
        meta.status_code == 200
        and meta.json()["kind"] == "export"
        and meta.json()["original_name"].endswith(fmt)
    )
    raw = file_bytes(env, admin_engine, body["file_id"])
    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(raw.decode())))
        header, data = rows[0], rows[1:]
        amount_col = header.index("Claim amount")
        docs = {
            f"{r[header.index('Invoice number')]}/{r[header.index('Line')]}": Decimal(r[amount_col])
            for r in data
        }
    else:
        ws = load_workbook(io.BytesIO(raw)).active
        rows = list(ws.iter_rows(values_only=True))
        header, data = list(rows[0]), rows[1:]
        assert all(isinstance(r[header.index("Claim amount")], int | float | Decimal) for r in data)
        docs = {
            f"{r[header.index('Invoice number')]}/{r[header.index('Line')]}": Decimal(
                str(r[header.index("Claim amount")])
            )
            for r in data
        }
    assert docs == EXPECTED_AMOUNT


def test_missed_claims_export_is_audited(sc: Scenario) -> None:
    r = sc.viewer.get("/missed-claims", params={"format": "csv"})
    assert r.status_code == 202  # data.read holders may download the report
    assert "missed_claims.export_requested" in audit_actions(sc.ws.api, r.json()["file_id"])


# ---- S7.4-free guard: templates ---------------------------------------------------------------------------


def template_body(**over: Any) -> dict[str, Any]:
    return {
        "name": "Portal layout",
        "format": "csv",
        "columns": [
            {"header": "Ref", "source": "claim_reference"},
            {"header": "Invoice", "source": "document_number"},
            {"header": "Line", "source": "line_number"},
            {"header": "Invoice date", "source": "invoice_date", "format": "us"},
            {"header": "Qty", "source": "quantity", "format": "decimal:0"},
            {"header": "Amount", "source": "claim_amount"},
            {"header": "=Injected header", "constant": "=1+1"},
            {"header": "Customer", "source": "customer_name"},
        ],
        "options": {},
        **over,
    }


def test_template_crud_etag_and_validation(sc: Scenario) -> None:
    api, mfr = sc.ws.api, sc.ws.manufacturers["VOLT"]
    assert sc.viewer.get("/claim-export-templates").status_code == 403  # A P N only
    assert (
        sc.viewer.post(
            "/claim-export-templates", json={"manufacturer_id": mfr, **template_body()}
        ).status_code
        == 403
    )
    r = api.post("/claim-export-templates", json={"manufacturer_id": mfr, **template_body(name="CRUD")})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["revision"] == 1 and r.headers["ETag"] == '"1"' and t["columns"][3]["format"] == "us"
    dup = api.post("/claim-export-templates", json={"manufacturer_id": mfr, **template_body(name="CRUD")})
    assert dup.status_code == 409
    upd = {**template_body(name="CRUD renamed"), "active": True}
    assert api.put(f"/claim-export-templates/{t['id']}", json=upd).status_code == 412  # If-Match required
    assert (
        api.put(f"/claim-export-templates/{t['id']}", json=upd, headers={"If-Match": '"7"'}).status_code
        == 412
    )
    ok = api.put(f"/claim-export-templates/{t['id']}", json=upd, headers={"If-Match": '"1"'})
    assert ok.status_code == 200 and ok.json()["revision"] == 2 and ok.json()["name"] == "CRUD renamed"
    assert (
        api.put(f"/claim-export-templates/{t['id']}", json=upd, headers={"If-Match": '"1"'}).status_code
        == 412
    )
    listed = api.get("/claim-export-templates", params={"manufacturer_id": mfr}).json()["data"]
    assert t["id"] in {x["id"] for x in listed}
    bad_cols = [
        [{"header": "x", "source": "not_a_field"}],
        [{"header": "x"}],
        [{"header": "x", "source": "claim_amount", "format": "iso"}],
        [{"header": "x", "source": "claim_amount", "constant": "1"}],
    ]
    for cols in bad_cols:
        r = api.post(
            "/claim-export-templates",
            json={"manufacturer_id": mfr, **template_body(name="bad", columns=cols)},
        )
        assert r.status_code == 422, (cols, r.text)
    ghost = api.post(
        "/claim-export-templates",
        json={"manufacturer_id": "00000000-0000-7000-8000-000000000000", **template_body(name="ghost")},
    )
    assert ghost.status_code == 422
    extra = api.post(
        "/claim-export-templates", json={"manufacturer_id": mfr, **template_body(name="e"), "tenant_id": "x"}
    )
    assert extra.status_code == 422
    assert "claim_export_template.created" in audit_actions(api, t["id"])


# ---- S7.3 batches -----------------------------------------------------------------------------------------


def test_create_requires_idempotency_key_and_exactly_one_selection(sc: Scenario) -> None:
    api, k = sc.ws.api, POOL4[0]
    rid = pool(api)[k]["id"]
    body = {"manufacturer_id": sc.mfr[k], "result_ids": [rid]}
    assert api.post("/claim-batches", json=body).status_code == 400
    both = {**body, "filter": {}}
    assert api.post("/claim-batches", json=both, headers=key()).status_code == 422
    neither = {"manufacturer_id": sc.mfr[k]}
    assert api.post("/claim-batches", json=neither, headers=key()).status_code == 422
    assert sc.viewer.post("/claim-batches", json=body, headers=key()).status_code == 403
    nobody = {"manufacturer_id": "00000000-0000-7000-8000-000000000000", "result_ids": [rid]}
    assert api.post("/claim-batches", json=nobody, headers=key()).status_code == 422


def test_create_validates_each_result(sc: Scenario) -> None:
    api = sc.ws.api
    k = POOL4[0]
    rid = pool(api)[k]["id"]
    other = next(x for x in pool(api).values() if x["manufacturer_id"] != sc.mfr[k])
    r = api.post(
        "/claim-batches", json={"manufacturer_id": sc.mfr[k], "result_ids": [other["id"]]}, headers=key()
    )
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "wrong_manufacturer"
    ghost = "00000000-0000-7000-8000-000000000000"
    r = api.post(
        "/claim-batches", json={"manufacturer_id": sc.mfr[k], "result_ids": [rid, ghost]}, headers=key()
    )
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "not_found"
    run_id = api.get("/calculation-runs", params={"status": "completed", "limit": 1}).json()["data"][0]["id"]
    exc = next(
        x
        for x in api.get(f"/calculation-runs/{run_id}/results", params={"status": "exception"}).json()["data"]
    )
    r = api.post(
        "/claim-batches",
        json={"manufacturer_id": exc["manufacturer_id"] or sc.mfr[k], "result_ids": [exc["id"]]},
        headers=key(),
    )
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] in (
        "not_eligible",
        "wrong_manufacturer",
    )


def test_double_claim_is_refused_with_the_conflicting_lines_and_void_releases_them(sc: Scenario) -> None:
    api, k = sc.ws.api, POOL4[0]
    first = one(sc, k)
    rid = next(x for x in lines(api, first["id"]))["calculation_result_id"]
    again = api.post(
        "/claim-batches", json={"manufacturer_id": sc.mfr[k], "result_ids": [rid]}, headers=key()
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_claimed"
    d = again.json()["error"]["details"][0]
    claim_ref = lines(api, first["id"])[0]["claim_reference"]
    assert d["claim_reference"] == claim_ref and d["batch_number"] == first["batch_number"]
    # the line is no longer offered by the scan, and a filter batch cannot contain it
    assert k not in pool(api)
    # voiding releases it: claimable again, under a new reference; the old line stays as history
    release(sc, first)
    old = lines(api, first["id"])[0]
    assert old["released_at"] is not None and old["frozen_at"] is None
    assert k in pool(api)
    second = one(sc, k)
    new = lines(api, second["id"])[0]
    assert new["claim_reference"] != claim_ref and new["released_at"] is None
    release(sc, second)


def test_a_non_unique_integrity_error_on_the_line_flush_is_not_reported_as_already_claimed(
    sc: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the active-line unique indexes mean 'double claim'. A line the schema refuses for another reason
    (here ck_claim_lines_*: a sales line without its sales line) must surface as the server error it is."""
    from rebate.modules.claims import batches

    api, k = sc.ws.api, POOL4[0]
    real = batches._claim_line

    def broken(*a: Any) -> Any:
        line = real(*a)
        line.sales_document_line_id = None
        return line

    monkeypatch.setattr(batches, "_claim_line", broken)
    r = make(api, [k], sc.mfr[k])
    assert r.status_code == 500, r.text
    assert "already_claimed" not in r.text
    monkeypatch.undo()
    assert k in pool(api)  # nothing was claimed; the same line batches normally afterwards
    release(sc, one(sc, k))


def test_concurrent_creates_cannot_double_claim(sc: Scenario, env: Env, admin_engine: Engine) -> None:
    k = POOL4[1]
    rid = pool(sc.ws.api)[k]["id"]
    body = {"manufacturer_id": sc.mfr[k], "result_ids": [rid]}
    second = member(env.stack, env.app, sc.ws.api, "analyst")
    clients = [sc.ws.api, second]
    barrier, out = threading.Barrier(2), []

    def attempt(api: Api) -> None:
        barrier.wait()
        out.append(api.post("/claim-batches", json=body, headers=key()))

    threads = [threading.Thread(target=attempt, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for r in out) == [201, 409], [r.text for r in out]
    with admin_engine.connect() as conn:
        active = conn.execute(
            text(
                "SELECT count(*) FROM claim_lines cl JOIN calculation_results r ON r.id = cl.calculation_result_id "
                "WHERE cl.tenant_id = :t AND cl.released_at IS NULL AND r.id = :r"
            ),
            {"t": sc.ws.tenant_id, "r": rid},
        ).scalar_one()
    assert active == 1
    winner = next(r for r in out if r.status_code == 201).json()
    release(sc, winner)


def test_create_from_filter_and_numbering(sc: Scenario) -> None:
    api = sc.ws.api
    k = POOL4[2]
    mfr = sc.mfr[k]
    expected = {x for x in POOL4 if sc.mfr[x] == mfr}
    flt = {"manufacturer_id": mfr, "filter": {"invoice_from": "2026-01-01", "invoice_to": "2026-12-31"}}
    r = api.post("/claim-batches", json=flt, headers=key())
    assert r.status_code == 201, r.text
    b = r.json()
    got = {f"{x['document_number']}/{x['line_number']}" for x in lines(api, b["id"])}
    assert expected <= got and got <= set(ELIGIBLE)
    assert Decimal(b["total_amount"]) == sum((EXPECTED_AMOUNT[x] for x in got), Decimal(0))
    refs = [x["claim_reference"] for x in lines(api, b["id"])]
    nums = [int(x.rsplit("-", 1)[1]) for x in refs]
    assert (
        all(x.startswith("CL-2026-") for x in refs) and nums == sorted(nums) and len(set(nums)) == len(nums)
    )
    assert b["batch_number"].startswith("CB-2026-") and b["status"] == "draft"
    empty = api.post(
        "/claim-batches",
        json={"manufacturer_id": mfr, "filter": {"invoice_from": "2031-01-01"}},
        headers=key(),
    )
    assert empty.status_code == 422 and empty.json()["error"]["details"][0]["code"] == "no_lines"
    release(sc, b)


def test_create_is_idempotent_per_key(sc: Scenario) -> None:
    api, k = sc.ws.api, POOL4[0]
    rid = pool(api)[k]["id"]
    body = {"manufacturer_id": sc.mfr[k], "result_ids": [rid]}
    ik = key()
    a = api.post("/claim-batches", json=body, headers=ik)
    b = api.post("/claim-batches", json=body, headers=ik)
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    other = api.post("/claim-batches", json={**body, "result_ids": [rid, rid]}, headers=ik)
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    batches = api.get("/claim-batches", params={"status": "draft"}).json()["data"]
    assert sum(1 for x in batches if x["id"] == a.json()["id"]) == 1
    release(sc, a.json())


def test_approve_enforces_four_eyes_and_roles(sc: Scenario) -> None:
    api, k = sc.ws.api, POOL4[3]
    etag = api.get("/settings").headers["ETag"]
    assert (
        api.patch("/settings", json={"four_eyes_required": True}, headers={"If-Match": etag}).status_code
        == 200
    )
    try:
        draft = one(sc, k)
        denied = api.post(f"/claim-batches/{draft['id']}/approve")  # maker = approver
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "four_eyes_violation"
        assert "claim_batch.four_eyes_violation" in audit_actions(
            api, draft["id"]
        )  # survives the error response
        assert sc.analyst.post(f"/claim-batches/{draft['id']}/approve").status_code == 403  # permission
        assert api.get(f"/claim-batches/{draft['id']}").json()["status"] == "draft"
        ok = sc.approver.post(f"/claim-batches/{draft['id']}/approve")
        assert ok.status_code == 200 and ok.json()["status"] == "approved"
        assert ok.json()["approved_by"] != draft["created_by"]
        again = sc.approver.post(f"/claim-batches/{draft['id']}/approve")
        assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
        release(sc, draft)
    finally:
        etag = api.get("/settings").headers["ETag"]
        api.patch("/settings", json={"four_eyes_required": False}, headers={"If-Match": etag})
    # with four-eyes off the maker may approve (single-user workspaces)
    mine = one(sc, k)
    assert api.post(f"/claim-batches/{mine['id']}/approve").status_code == 200
    release(sc, mine)


def test_void_rules(sc: Scenario) -> None:
    api, k = sc.ws.api, POOL4[3]
    b = one(sc, k)
    assert sc.analyst.post(f"/claim-batches/{b['id']}/void", json={"reason": "x"}).status_code == 403
    assert sc.approver.post(f"/claim-batches/{b['id']}/void", json={}).status_code == 422  # reason required
    ok = sc.approver.post(f"/claim-batches/{b['id']}/void", json={"reason": "wrong manufacturer"})
    assert (
        ok.status_code == 200
        and ok.json()["status"] == "void"
        and ok.json()["void_reason"] == "wrong manufacturer"
    )
    assert sc.approver.post(f"/claim-batches/{b['id']}/void", json={"reason": "again"}).status_code == 409
    assert api.post(f"/claim-batches/{b['id']}/approve").status_code == 409  # a void batch never moves again
    assert "claim_batch.voided" in audit_actions(api, b["id"])


def test_export_needs_an_approved_batch_and_a_usable_template(sc: Scenario) -> None:
    api, k = sc.ws.api, POOL4[3]
    b = one(sc, k)
    assert api.post(f"/claim-batches/{b['id']}/export", json={}).status_code == 409  # draft
    assert api.post(f"/claim-batches/{b['id']}/approve").status_code == 200
    assert sc.viewer.post(f"/claim-batches/{b['id']}/export", json={}).status_code == 403
    other_mfr = next(m for code, m in sc.ws.manufacturers.items() if m != sc.mfr[k])
    foreign = api.post(
        "/claim-export-templates", json={"manufacturer_id": other_mfr, **template_body(name="elsewhere")}
    ).json()
    r = api.post(f"/claim-batches/{b['id']}/export", json={"template_id": foreign["id"]})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "not_usable"
    release(sc, b)


def test_idempotent_submit_freezes_and_replays(sc: Scenario, admin_engine: Engine) -> None:
    api, k = sc.ws.api, POOL4[2]  # consumed by this test: later tests use the other three
    b = one(sc, k)
    path = f"/claim-batches/{b['id']}/submit"
    body = {"submitted_at": "2026-07-09", "external_reference": "PORTAL-7781"}
    assert api.post(path, json=body, headers=key()).status_code == 409  # draft: approve first
    assert api.post(f"/claim-batches/{b['id']}/approve").status_code == 200
    assert api.post(path, json=body).status_code == 400  # Idempotency-Key required
    assert sc.analyst.post(path, json=body, headers=key()).status_code == 403
    future = {**body, "submitted_at": "2026-07-11"}  # business date is 2026-07-10
    assert api.post(path, json=future, headers=key()).status_code == 422
    ik = key()
    a = api.post(path, json=body, headers=ik)
    assert a.status_code == 200, a.text
    assert a.json()["status"] == "submitted" and a.json()["submitted_at"] == "2026-07-09"
    assert a.json()["external_reference"] == "PORTAL-7781"
    ln = lines(api, b["id"])[0]
    assert ln["frozen_at"] is not None
    replay = api.post(path, json=body, headers=ik)
    assert replay.status_code == 200 and replay.json() == a.json()
    same_details_new_key = api.post(path, json=body, headers=key())
    assert same_details_new_key.status_code == 200 and same_details_new_key.json() == a.json()
    other_details = api.post(path, json={**body, "external_reference": "OTHER"}, headers=key())
    assert other_details.status_code == 409 and other_details.json()["error"]["code"] == "invalid_state"
    changed_body = api.post(path, json={**body, "external_reference": "OTHER"}, headers=ik)
    assert changed_body.status_code == 409 and changed_body.json()["error"]["code"] == "idempotency_conflict"
    assert lines(api, b["id"])[0]["frozen_at"] == ln["frozen_at"]  # frozen once
    assert audit_actions(api, b["id"]).count("claim_batch.submitted") == 1
    assert sc.approver.post(f"/claim-batches/{b['id']}/void", json={"reason": "x"}).status_code == 409
    assert k not in pool(api)  # submitted lines stay claimed for good


# ---- the acceptance flow: scan -> batch -> approve -> export -> submit; totals tie out ----------------------


FLOW: dict[str, Any] = {}


def test_flow_scan_to_approved_batch_ties_out_to_ground_truth(sc: Scenario) -> None:
    api = sc.ws.api
    volt = sc.ws.manufacturers["VOLT"]
    before = api.get("/missed-claims/summary", params={"manufacturer_id": volt}).json()
    r = make(api, FIX7, volt)
    assert r.status_code == 201, r.text
    b = r.json()
    FLOW["batch"] = b
    got = lines(api, b["id"])
    by_key = {f"{x['document_number']}/{x['line_number']}": x for x in got}
    assert set(by_key) == set(FIX7)
    # batch total = sum of lines = sum of the ground-truth claim amounts of exactly these lines
    expected = sum((EXPECTED_AMOUNT[k] for k in FIX7), Decimal(0))
    assert expected == Decimal("10410.00")
    assert Decimal(b["total_amount"]) == expected and b["line_count"] == 7
    assert sum((Decimal(x["claim_amount"]) for x in got), Decimal(0)) == expected
    for k, x in by_key.items():
        assert Decimal(x["claim_amount"]) == EXPECTED_AMOUNT[k], k
        assert Decimal(x["quantity"]) == Decimal(CALC[k]["eligible_quantity"]), k
        assert Decimal(x["unit_amount"]) == Decimal(CALC[k]["claim_unit_amount"]), k
        assert x["frozen_at"] is None and x["released_at"] is None
    refs = sorted(x["claim_reference"] for x in got)
    assert refs[0].startswith("CL-2026-") and len(set(refs)) == 7
    assert b["period_from"] <= b["period_to"]
    # the scan shrank by exactly the batch
    after = api.get("/missed-claims/summary", params={"manufacturer_id": volt}).json()
    assert Decimal(before["totals"]["amount"]) - Decimal(after["totals"]["amount"]) == expected
    assert before["totals"]["lines"] - after["totals"]["lines"] == 7
    assert not set(FIX7) & set(pool(api))
    # created before approval: a submitted-state action is not yet possible
    assert (
        api.post(
            f"/claim-batches/{b['id']}/submit",
            json={"submitted_at": "2026-07-05", "external_reference": "X"},
            headers=key(),
        ).status_code
        == 409
    )


def test_flow_export_csv_with_template_columns_and_escaping(
    sc: Scenario, env: Env, admin_engine: Engine
) -> None:
    api, b = sc.ws.api, FLOW["batch"]
    volt = sc.ws.manufacturers["VOLT"]
    tpl = api.post(
        "/claim-export-templates", json={"manufacturer_id": volt, **template_body(name="VOLT portal CSV")}
    ).json()
    assert api.post(f"/claim-batches/{b['id']}/approve").status_code == 200
    r = api.post(f"/claim-batches/{b['id']}/export", json={"template_id": tpl["id"]})
    assert r.status_code == 202, r.text
    file_id = r.json()["file_id"]
    drain(env.app)
    raw = file_bytes(env, admin_engine, file_id).decode()
    rows = list(csv.reader(io.StringIO(raw)))
    assert rows[0] == [
        "Ref",
        "Invoice",
        "Line",
        "Invoice date",
        "Qty",
        "Amount",
        "'=Injected header",
        "Customer",
    ]
    assert len(rows) == 8
    api_lines = {x["claim_reference"]: x for x in lines(api, b["id"])}
    seen = set()
    for row in rows[1:]:
        ref, doc, line, date_us, qty, amount, injected, _customer = row
        k = f"{doc}/{line}"
        seen.add(k)
        assert api_lines[ref]["document_number"] == doc
        assert Decimal(amount) == EXPECTED_AMOUNT[k]  # ground truth, to the cent
        assert Decimal(qty) == Decimal(CALC[k]["eligible_quantity"])
        assert injected == "'=1+1"  # constant escaped
        assert len(date_us) == 10 and date_us[2] == "/" and date_us[5] == "/"
    assert seen == set(FIX7)
    assert sum(Decimal(r[5]) for r in rows[1:]) == Decimal("10410.00")
    linked = api.get(f"/claim-batches/{b['id']}").json()
    assert linked["exported_file_id"] == file_id and linked["export_template_id"] == tpl["id"]
    assert "claim_batch.exported" in audit_actions(api, b["id"])
    FLOW["csv"] = file_id


def test_flow_export_xlsx_has_typed_cells(sc: Scenario, env: Env, admin_engine: Engine) -> None:
    api, b = sc.ws.api, FLOW["batch"]
    volt = sc.ws.manufacturers["VOLT"]
    body = template_body(name="VOLT portal XLSX", format="xlsx", options={"sheet_name": "VOLT claims"})
    tpl = api.post("/claim-export-templates", json={"manufacturer_id": volt, **body}).json()
    r = api.post(f"/claim-batches/{b['id']}/export", json={"template_id": tpl["id"]})
    drain(env.app)
    ws = load_workbook(io.BytesIO(file_bytes(env, admin_engine, r.json()["file_id"]))).active
    assert ws.title == "VOLT claims"
    rows = list(ws.iter_rows())
    assert rows[0][6].value == "'=Injected header" and rows[1][6].value == "'=1+1"
    amounts = [row[5] for row in rows[1:]]
    assert all(c.data_type == "n" for c in amounts)
    assert sum(Decimal(str(c.value)) for c in amounts) == Decimal("10410.00")
    assert all(row[2].data_type == "n" for row in rows[1:])  # line number is a number, not text


def test_flow_export_without_template_uses_the_newest_active_template(
    sc: Scenario, env: Env, admin_engine: Engine
) -> None:
    api, b = sc.ws.api, FLOW["batch"]
    r = api.post(f"/claim-batches/{b['id']}/export", json={})
    assert r.status_code == 202
    drain(env.app)
    raw = file_bytes(env, admin_engine, r.json()["file_id"])
    assert raw[:2] == b"PK"  # the newest active VOLT template is the XLSX one: the file is a workbook


def test_flow_submit_freezes_lines_and_the_scan_stays_clean(sc: Scenario) -> None:
    api, b = sc.ws.api, FLOW["batch"]
    volt = sc.ws.manufacturers["VOLT"]
    ik = key()
    body = {"submitted_at": BATCH_FIXTURE["submitted_at"], "external_reference": "VOLT-PORTAL-2026-06"}
    r = sc.approver.post(f"/claim-batches/{b['id']}/submit", json=body, headers=ik)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "submitted" and done["submitted_at"] == "2026-07-05"
    assert Decimal(done["total_amount"]) == Decimal("10410.00") and done["line_count"] == 7
    got = lines(api, b["id"])
    assert len(got) == 7 and all(x["frozen_at"] is not None and x["released_at"] is None for x in got)
    assert sum((Decimal(x["claim_amount"]) for x in got), Decimal(0)) == Decimal(done["total_amount"])
    assert not set(FIX7) & set(pool(api))
    summary = api.get("/missed-claims/summary", params={"manufacturer_id": volt}).json()
    claimed_keys = set(FIX7)
    assert all(k not in claimed_keys for k in pool(api))
    assert summary["totals"]["lines"] == sum(1 for k in pool(api) if sc.mfr[k] == volt)
    # a submitted batch is final: void, approve and re-creating its lines are refused
    assert sc.approver.post(f"/claim-batches/{b['id']}/void", json={"reason": "x"}).status_code == 409
    assert api.post(f"/claim-batches/{b['id']}/approve").status_code == 409
    again = api.post(
        "/claim-batches",
        json={"manufacturer_id": volt, "result_ids": [got[0]["calculation_result_id"]]},
        headers=key(),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_claimed"
    # exporting a submitted batch still works, and does not touch the (immutable) batch
    r2 = api.post(f"/claim-batches/{b['id']}/export", json={})
    assert r2.status_code == 202
    FLOW["submitted"] = done


def test_flow_a_new_run_makes_old_results_stale_and_claimed_lines_stay_claimed(sc: Scenario) -> None:
    api = sc.ws.api
    k = POOL4[0]
    old_id = pool(api)[k]["id"]
    again = run(sc.ws, **WINDOW)
    assert again["status"] == "completed"
    stale = api.post(
        "/claim-batches", json={"manufacturer_id": sc.mfr[k], "result_ids": [old_id]}, headers=key()
    )
    assert stale.status_code == 422 and stale.json()["error"]["details"][0]["code"] == "stale_result"
    now = pool(api)
    assert k in now and now[k]["id"] != old_id and not set(FIX7) & set(now)  # claimed lines stay claimed
    fresh = one(sc, k)
    release(sc, fresh)


# ---- freeze and transition invariants in the database, as the application role ------------------------------


@contextmanager
def as_app(engine: Engine, tenant: str) -> Iterator[Connection]:
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
            yield conn
        finally:
            tx.rollback()


def attempt(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> str:
    sp = conn.begin_nested()
    try:
        n = conn.execute(text(sql), params or {}).rowcount
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    except DBAPIError as exc:
        sp.rollback()
        return str(exc.orig).splitlines()[0]
    sp.rollback()
    return f"ok n={n}"


def test_db_submitted_lines_and_batch_are_immutable_for_the_app_role(
    sc: Scenario, app_engine: Engine
) -> None:
    b = FLOW["submitted"]
    with as_app(app_engine, sc.ws.tenant_id) as conn:
        line = conn.execute(
            text("SELECT id FROM claim_lines WHERE batch_id = :b LIMIT 1"), {"b": b["id"]}
        ).scalar_one()
        p = {"l": line, "b": b["id"]}
        for sql in (
            "UPDATE claim_lines SET claim_amount = 1 WHERE id = :l",  # not a granted column
            "UPDATE claim_lines SET claim_reference = 'X' WHERE id = :l",
            "DELETE FROM claim_lines WHERE id = :l",
            "TRUNCATE claim_lines",
            "UPDATE claim_batches SET total_amount = 1 WHERE id = :b",
            "UPDATE claim_batches SET line_count = 1 WHERE id = :b",
            "DELETE FROM claim_batches WHERE id = :b",
        ):
            assert "permission denied" in attempt(conn, sql, p), sql
        for sql, why in (
            ("UPDATE claim_lines SET frozen_at = NULL WHERE id = :l", "never changes"),
            ("UPDATE claim_lines SET released_at = now() WHERE id = :l", "never changes"),
            ("UPDATE claim_lines SET frozen_at = now() WHERE id = :l", "never changes"),
            ("UPDATE claim_batches SET status = 'draft' WHERE id = :b", "submitted batch changes only"),
            (
                "UPDATE claim_batches SET status = 'void', void_reason = 'x', voided_at = now() WHERE id = :b",
                "submitted batch changes only",
            ),
            (
                "UPDATE claim_batches SET submitted_at = '2026-01-01' WHERE id = :b",
                "submitted batch changes only",
            ),
            (
                "UPDATE claim_batches SET exported_file_id = NULL WHERE id = :b",
                "submitted batch changes only",
            ),
        ):
            assert why in attempt(conn, sql, p), sql
        # the one audited exception: the external reference may be corrected
        assert (
            attempt(conn, "UPDATE claim_batches SET external_reference = 'CORRECTED' WHERE id = :b", p)
            == "ok n=1"
        )
    with as_app(app_engine, sc.ws.tenant_id) as conn:  # nothing above persisted
        assert (
            conn.execute(
                text("SELECT external_reference FROM claim_batches WHERE id = :b"), {"b": b["id"]}
            ).scalar_one()
            == "VOLT-PORTAL-2026-06"
        )


def test_db_status_transitions_and_required_columns(sc: Scenario, app_engine: Engine) -> None:
    draft, approved = one(sc, POOL4[0]), one(sc, POOL4[1])
    assert sc.ws.api.post(f"/claim-batches/{approved['id']}/approve").status_code == 200
    gone = one(sc, POOL4[3])
    release(sc, gone)
    try:
        with as_app(app_engine, sc.ws.tenant_id) as conn:
            d, a, g = ({"b": x["id"], "u": x["created_by"]} for x in (draft, approved, gone))
            cases = [
                # draft -> submitted skips approval
                (
                    d,
                    "UPDATE claim_batches SET status = 'submitted', submitted_at = '2026-07-01', submitted_by = :u, external_reference = 'X' WHERE id = :b",
                    "illegal status transition",
                ),
                # draft -> approved without who/when
                (
                    d,
                    "UPDATE claim_batches SET status = 'approved' WHERE id = :b",
                    "ck_claim_batches_approved",
                ),
                # approved -> draft
                (a, "UPDATE claim_batches SET status = 'draft' WHERE id = :b", "illegal status transition"),
                # approved -> submitted without the submission details
                (a, "UPDATE claim_batches SET status = 'submitted' WHERE id = :b", "ck_claim_batches"),
                # approved -> void without a reason
                (a, "UPDATE claim_batches SET status = 'void' WHERE id = :b", "ck_claim_batches_void"),
                # a transition may not smuggle in other workflow columns
                (
                    d,
                    "UPDATE claim_batches SET status = 'approved', approved_by = :u, approved_at = now(), void_reason = 'x' WHERE id = :b",
                    "may not change those columns",
                ),
                # a void batch never changes
                (
                    g,
                    "UPDATE claim_batches SET external_reference = 'x' WHERE id = :b",
                    "void batch never changes",
                ),
                (
                    g,
                    "UPDATE claim_batches SET status = 'draft', void_reason = NULL, voided_at = NULL WHERE id = :b",
                    "void batch never changes",
                ),
                # immutable columns
                (d, "UPDATE claim_batches SET batch_number = 'CB-X' WHERE id = :b", "permission denied"),
            ]
            for params, sql, why in cases:
                assert why in attempt(conn, sql, params), sql
            # lines: frozen only by a submitted batch; released only along with a void
            assert "frozen only by submitting" in attempt(
                conn, "UPDATE claim_lines SET frozen_at = now() WHERE batch_id = :b", d
            )
            assert "released exactly when the batch is void" in attempt(
                conn, "UPDATE claim_lines SET released_at = now() WHERE batch_id = :b", d
            )
            # a void batch's lines are released and stay that way
            assert "released once" in attempt(
                conn, "UPDATE claim_lines SET released_at = NULL WHERE batch_id = :b", g
            )
            # lines only join a draft batch
            ins = (
                "INSERT INTO claim_lines (tenant_id, id, batch_id, claim_reference, source_kind, sales_document_line_id, "
                "calculation_result_id, agreement_version_id, claim_amount) "
                "SELECT tenant_id, gen_random_uuid(), batch_id, 'CL-X-1', source_kind, sales_document_line_id, "
                "calculation_result_id, agreement_version_id, claim_amount FROM claim_lines WHERE batch_id = :b LIMIT 1"
            )
            assert "only be added to a draft batch" in attempt(conn, ins, a)
    finally:
        release(sc, draft)
        release(sc, approved)


def test_db_a_source_line_has_one_active_claim_line(sc: Scenario, app_engine: Engine) -> None:
    b = one(sc, POOL4[0])
    other = one(sc, POOL4[1])
    try:
        with as_app(app_engine, sc.ws.tenant_id) as conn:
            dup = (
                "INSERT INTO claim_lines (tenant_id, id, batch_id, claim_reference, source_kind, sales_document_line_id, "
                "calculation_result_id, agreement_version_id, claim_amount) "
                "SELECT cl.tenant_id, gen_random_uuid(), :other, 'CL-DUP-1', cl.source_kind, cl.sales_document_line_id, "
                "cl.calculation_result_id, cl.agreement_version_id, cl.claim_amount FROM claim_lines cl WHERE cl.batch_id = :b"
            )
            msg = attempt(conn, dup, {"b": b["id"], "other": other["id"]})
            assert "uq_claim_lines_active_sales_line" in msg, msg
            # the shape checks: a sales line claim needs its sales line, a rebate claim its accrual entry
            bad = dup.replace("cl.source_kind", "'rebate_accrual'").replace("'CL-DUP-1'", "'CL-DUP-2'")
            assert "ck_claim_lines_" in attempt(conn, bad, {"b": b["id"], "other": other["id"]})
    finally:
        release(sc, b)
        release(sc, other)


def test_db_batch_without_matching_lines_cannot_commit(sc: Scenario, app_engine: Engine) -> None:
    """The deferred tie-out: line_count / total_amount must equal the lines when the transaction commits."""
    k = POOL4[0]
    with as_app(app_engine, sc.ws.tenant_id) as conn:
        mfr = sc.mfr[k]
        user = conn.execute(text("SELECT created_by FROM claim_export_templates LIMIT 1")).scalar_one()
        ins = (
            "INSERT INTO claim_batches (tenant_id, id, manufacturer_id, batch_number, line_count, total_amount, created_by) "
            "VALUES (:t, gen_random_uuid(), :m, 'CB-ORPHAN-1', 3, 30, :u)"
        )
        msg = attempt(conn, ins, {"t": sc.ws.tenant_id, "m": mfr, "u": user})
        assert "differ from the lines" in msg, msg


# ---- R3: per-user throttle and in-flight dedupe on job-enqueuing endpoints; NUL in free text is a 422 ---------


def test_export_requests_are_throttled_per_user_with_retry_after(sc: Scenario, env: Env) -> None:
    from rebate.api.deps import JOB_REQUESTS_PER_MINUTE

    spammer = member(env.stack, env.app, sc.ws.api, "viewer")  # fresh user: its own counter
    for _ in range(JOB_REQUESTS_PER_MINUTE):
        assert spammer.get("/missed-claims", params={"format": "csv"}).status_code == 202
    r = spammer.get("/missed-claims", params={"format": "csv"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) >= 1
    other = member(env.stack, env.app, sc.ws.api, "viewer")
    assert other.get("/missed-claims", params={"format": "csv"}).status_code == 202
    drain(env.app)


def test_the_same_export_in_flight_is_the_same_job(sc: Scenario, env: Env) -> None:
    api = member(env.stack, env.app, sc.ws.api, "viewer")
    a = api.get("/missed-claims", params={"format": "xlsx"}).json()
    b = api.get("/missed-claims", params={"format": "xlsx"}).json()
    assert a["file_id"] == b["file_id"] and a["job"]["id"] == b["job"]["id"]
    c = api.get("/missed-claims", params={"format": "csv"}).json()
    assert c["file_id"] != a["file_id"]
    drain(env.app)
    d = api.get("/missed-claims", params={"format": "xlsx"}).json()  # finished: a new export is a new job
    assert d["job"]["id"] != a["job"]["id"]
    drain(env.app)


def test_a_nul_byte_in_free_text_is_a_422_not_a_500(sc: Scenario) -> None:
    api, nul = sc.ws.api, "bad\u0000reason"
    assert sc.approver.post(f"/claim-batches/{uuid.uuid4()}/void", json={"reason": nul}).status_code == 422
    case = uuid.uuid4()
    for path, body in (
        (f"/cases/{case}/write-off", {"reason": nul}),
        (f"/cases/{case}/notes", {"note": nul}),
        (f"/cases/{case}/transition", {"to_status": "open", "note": nul}),
    ):
        r = api.post(path, json=body, headers={"If-Match": '"1"'})
        assert r.status_code == 422, (path, r.status_code, r.text)
    opts = {"delimiter": ",", "sheet_name": nul}
    t = api.post(
        "/claim-export-templates",
        json={
            "manufacturer_id": next(iter(sc.ws.manufacturers.values())),
            **template_body(name="nul", options=opts),
        },
    )
    assert t.status_code == 422
