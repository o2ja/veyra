"""S5.7 calculation runs end to end: real import pipeline (SYNTHETIC CSVs), real agreements pipeline, real job queue
and Postgres. Expected values come from ``testdata/generated/ground_truth``, ``docs/BUSINESS_LOGIC.md`` and hand
derivation (comments), never from the output of the code under test."""

import ast
import hashlib
import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.modules.claims import frozen, persistence, runs
from tests.conftest import DbUrls
from tests.integration.agreements_support import create, member, payload
from tests.integration.calc_support import (
    SCENARIOS,
    FixtureClaims,
    activate_all,
    all_settings,
    create_run,
    import_sales,
    line_ids,
    open_workspace,
    results,
    run,
    settings,
)
from tests.integration.conftest import Workspace, clean_file, import_file, put, run_import
from tests.support import FakeClock, drain, fake_clock, make_app
from tests.testdata import data, truth

NOW = datetime(
    2026, 7, 10, 15, 0, tzinfo=UTC
)  # business date 2026-07-10 (America/Chicago): the ground-truth run date
SPA_NAMES = ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901", "SPA-BRIT-2026-031", "SPA-COPR-2026-107")
CALC = truth("expected_calculation.json")
WINDOW = {"date_from": "2026-01-01", "date_to": "2026-12-31"}


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


@pytest.fixture(scope="module")
def calc(env: Env) -> Scenario:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    versions = activate_all(ws, SPA_NAMES)
    import_sales(ws)
    return Scenario(ws, versions)


@pytest.fixture
def claimed(calc: Scenario, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> FixtureClaims:
    """The submitted batch CB-VOLT-2026-06 as S7 will provide it (precondition of SC-09r)."""
    v = calc.versions["SPA-VOLT-2026-884"]
    source = FixtureClaims(
        line_ids(admin_engine, calc.ws.tenant_id),
        agreement_id=v["agreement_id"],
        version_id=v["id"],
        manufacturer_id=uuid.UUID(calc.ws.manufacturers["VOLT"]),
    )
    monkeypatch.setattr(frozen, "source", source)
    return source


@pytest.fixture(scope="module")
def baseline(calc: Scenario, admin_engine: Engine) -> dict[str, Any]:
    """The reference run of the scenario file (run date 2026-07-10), with the claimed batch in place."""
    v = calc.versions["SPA-VOLT-2026-884"]
    source = FixtureClaims(
        line_ids(admin_engine, calc.ws.tenant_id),
        agreement_id=v["agreement_id"],
        version_id=v["id"],
        manufacturer_id=uuid.UUID(calc.ws.manufacturers["VOLT"]),
    )
    previous, frozen.source = frozen.source, source
    try:
        r = run(calc.ws, **WINDOW)
        assert r["status"] == "completed", r
        return {"run": r, "results": results(calc.ws, r["id"])}
    finally:
        frozen.source = previous


# ---- ground-truth scenarios ---------------------------------------------------------------------------------


@pytest.mark.parametrize("sc", CALC["scenarios"], ids=lambda s: s["id"])
def test_scenario_from_imported_csv(baseline: dict[str, Any], calc: Scenario, sc: dict[str, Any]) -> None:
    r = baseline["results"][f"{sc['invoice_no']}/{sc['line_no']}"]
    assert r["status"] == sc["status"], sc["id"]
    assert r["exception_code"] == sc.get("exception_code"), sc["id"]
    assert Decimal(r["claim_amount"]) == Decimal(sc.get("claim_amount", "0")), sc["id"]
    for key in ("claim_unit_amount", "eligible_quantity", "excess_quantity"):
        if key in sc:
            assert Decimal(r[key]) == Decimal(sc[key]), (sc["id"], key)
    assert set(sc.get("flags", [])) <= set(r["flags"]), sc["id"]


def test_every_scenario_line_has_a_result_and_nothing_else_is_claimed(baseline: dict[str, Any]) -> None:
    expected = {f"{s['invoice_no']}/{s['line_no']}" for s in CALC["scenarios"]}
    assert expected <= set(baseline["results"])
    unexpected = {
        k: r["status"]
        for k, r in baseline["results"].items()
        if k not in expected and r["status"] == "eligible"
    }
    assert not unexpected, unexpected  # every eligible line in the file is a documented scenario


def test_run_resource_pins_inputs_and_totals(baseline: dict[str, Any], calc: Scenario) -> None:
    r, res = baseline["run"], baseline["results"]
    assert r["status"] == "completed" and r["error"] is None
    assert r["run_date"] == CALC["run_date"] and r["engine_version"] == "1.0.0"
    pinned = {v["id"] for v in calc.versions.values()}
    assert set(r["agreement_version_ids"]) == pinned
    assert len(r["input_checksum"]) == len(r["config_hash"]) == 64
    totals = r["totals"]
    assert totals["lines"] == len(res)
    assert totals["by_status"] == dict(Counter(x["status"] for x in res.values()))
    eligible = sum(
        (Decimal(x["claim_amount"]) for x in res.values() if x["status"] == "eligible"), Decimal(0)
    )
    assert Decimal(totals["eligible_amount"]) == eligible
    scenarios = CALC["scenarios"]
    at_risk = sum(
        (
            Decimal(x.get("claim_amount", "0"))
            for x in scenarios
            if x.get("exception_code") == "outside_claim_window"
        ),
        Decimal(0),
    )
    adjusted = sum(
        (Decimal(x.get("claim_amount", "0")) for x in scenarios if x["status"] == "adjustment"), Decimal(0)
    )
    assert at_risk > 0 and Decimal(totals["at_risk_amount"]) == at_risk  # money lost to expiry (GL-D1)
    assert Decimal(totals["adjustment_amount"]) == adjusted == Decimal("-50.00")  # SC-09r


def test_rerun_is_byte_identical(baseline: dict[str, Any], calc: Scenario, claimed: FixtureClaims) -> None:
    again = run(calc.ws, **WINDOW)
    assert again["id"] != baseline["run"]["id"] and again["status"] == "completed"
    for key in ("input_checksum", "config_hash"):
        assert again[key] == baseline["run"][key]
    assert (
        again["totals"] == baseline["run"]["totals"]
    )  # includes results_hash over every (line, inputs_hash)
    second = results(calc.ws, again["id"])
    assert {k: v["inputs_hash"] for k, v in second.items()} == {
        k: v["inputs_hash"] for k, v in baseline["results"].items()
    }
    one = baseline["results"]["INV-10001/1"]
    trace_a = calc.ws.api.get(f"/calculation-results/{one['id']}").json()["trace"]
    trace_b = calc.ws.api.get(f"/calculation-results/{second['INV-10001/1']['id']}").json()["trace"]
    assert trace_a == trace_b


def test_windowed_execution_is_identical_to_a_single_window(
    baseline: dict[str, Any], calc: Scenario, claimed: FixtureClaims, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One date per engine call (the memory bound for million-line runs): the cap state is carried between windows, so
    results, trace hashes, totals and the input checksum equal the single-window run (SC-07a/b share a cap)."""
    monkeypatch.setattr(runs, "WINDOW_LINES", 1)
    windowed = run(calc.ws, **WINDOW)
    assert windowed["status"] == "completed", windowed
    assert windowed["totals"] == baseline["run"]["totals"]
    assert windowed["input_checksum"] == baseline["run"]["input_checksum"]
    second = results(calc.ws, windowed["id"])
    assert {k: v["inputs_hash"] for k, v in second.items()} == {
        k: v["inputs_hash"] for k, v in baseline["results"].items()
    }
    assert Decimal(second["INV-10011/1"]["claim_amount"]) == Decimal(
        "2080.00"
    )  # SC-07b: cap remainder 40 x 52.00


# ---- reading results ----------------------------------------------------------------------------------------


def test_filters_and_keyset_pagination(baseline: dict[str, Any], calc: Scenario) -> None:
    rid, ws = baseline["run"]["id"], calc.ws
    res = baseline["results"]
    eligible = results(ws, rid, status="eligible")
    assert eligible and all(r["status"] == "eligible" for r in eligible.values())
    assert set(eligible) == {k for k, r in res.items() if r["status"] == "eligible"}
    assert set(results(ws, rid, exception_code="price_mismatch")) >= {"INV-10002/1"}
    volt = ws.manufacturers["VOLT"]
    assert set(results(ws, rid, manufacturer_id=volt)) < set(res)
    agreement = calc.versions["SPA-VOLT-2026-884"]["agreement_id"]
    for r in results(ws, rid, agreement_id=agreement).values():
        assert r["agreement_id"] == agreement
    ids: list[str] = []
    cursor = None
    while True:
        page = ws.api.get(
            f"/calculation-runs/{rid}/results", params={"limit": 5, **({"cursor": cursor} if cursor else {})}
        ).json()
        ids += [x["id"] for x in page["data"]]
        if not page["page"]["has_more"]:
            break
        cursor = page["page"]["next_cursor"]
    assert len(ids) == len(set(ids)) == len(res)
    assert ws.api.get(f"/calculation-runs/{rid}/results", params={"bogus": 1}).status_code == 400


def test_result_detail_carries_the_trace(baseline: dict[str, Any], calc: Scenario) -> None:
    one = baseline["results"]["INV-10001/1"]
    body = calc.ws.api.get(f"/calculation-results/{one['id']}").json()
    assert body["trace_ref"] == {"kind": "calculation_result", "id": one["id"]}
    trace = body["trace"]
    names = [s["step"] for s in trace["steps"]]
    assert names[:2] == ["candidate_versions", "customer_scope"] and "calc" in names and "round" in names
    assert trace["result"]["claim_amount"] == "2200.00" and trace["engine_version"] == "1.0.0"
    assert hashlib.sha256(b"x").hexdigest() and body["inputs_hash"] == one["inputs_hash"]
    runs_page = calc.ws.api.get("/calculation-runs", params={"status": "completed"}).json()
    assert baseline["run"]["id"] in {x["id"] for x in runs_page["data"]}


def test_viewer_reads_but_cannot_start_runs(calc: Scenario, env: Env, baseline: dict[str, Any]) -> None:
    viewer = member(env.stack, env.app, calc.ws.api, "viewer")
    assert viewer.get("/calculation-runs").status_code == 200
    assert viewer.get(f"/calculation-runs/{baseline['run']['id']}/results").status_code == 200
    denied = viewer.post("/calculation-runs", json=WINDOW, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert denied.status_code == 403
    assert viewer.post(f"/calculation-runs/{baseline['run']['id']}/propose-adjustments").status_code == 403


def test_idempotency_key_and_active_scope(calc: Scenario, baseline: dict[str, Any]) -> None:
    ws = calc.ws
    body = {"manufacturer_ids": [ws.manufacturers["COPR"]], **WINDOW}
    key = str(uuid.uuid4())
    first = ws.api.post("/calculation-runs", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    replay = ws.api.post("/calculation-runs", json=body, headers={"Idempotency-Key": key})
    assert replay.status_code == 202 and replay.json()["run"]["id"] == first.json()["run"]["id"]
    other = ws.api.post(
        "/calculation-runs", json={**body, "date_to": "2026-11-30"}, headers={"Idempotency-Key": key}
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    assert ws.api.post("/calculation-runs", json=body).status_code == 400  # key required
    twin = create_run(ws, **body)  # same scope while the first is still queued
    assert twin.status_code == 409 and twin.json()["error"]["code"] == "run_in_progress"
    drain(ws.app)
    assert ws.api.get(f"/calculation-runs/{first.json()['run']['id']}").json()["status"] == "completed"
    assert create_run(ws, **body).status_code == 202  # once finished the scope is free again
    drain(ws.app)


def test_scope_validation(calc: Scenario) -> None:
    ws = calc.ws
    assert create_run(ws, date_from="2026-02-01", date_to="2026-01-01").status_code == 422
    future = (NOW + timedelta(days=1)).isoformat()
    assert create_run(ws, as_of=future, **WINDOW).status_code == 422
    assert create_run(ws, as_of="2026-07-10T10:00:00", **WINDOW).status_code == 422  # no time zone
    assert create_run(ws, **WINDOW, bogus=1).status_code == 422
    assert create_run(ws, manufacturer_ids=[str(uuid.uuid4())], **WINDOW).status_code == 422


# ---- lock, failure, invisibility ----------------------------------------------------------------------------


def test_second_run_is_rejected_by_the_tenant_lock(
    calc: Scenario, admin_engine: Engine, baseline: dict[str, Any]
) -> None:
    ws = calc.ws
    r = create_run(ws, manufacturer_ids=[ws.manufacturers["BRIT"]], **WINDOW)
    assert r.status_code == 202, r.text
    run_id = uuid.UUID(r.json()["run"]["id"])
    key = runs.LOCK_KEY.format(tenant=ws.tenant_id)
    engine = ws.app.state.engine
    holder = threading.Event()
    release = threading.Event()

    def hold() -> None:  # another worker executing a run: holds the advisory lock in its transaction
        with admin_engine.connect() as conn, conn.begin():
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})
            holder.set()
            release.wait(30)

    t = threading.Thread(target=hold)
    t.start()
    assert holder.wait(10)
    try:
        with transaction(engine, uuid.UUID(ws.tenant_id)) as lock_db, pytest.raises(runs.RunInProgress):
            runs.execute(engine, uuid.UUID(ws.tenant_id), run_id, lock_db)
        assert (
            ws.api.get(f"/calculation-runs/{run_id}").json()["status"] == "queued"
        )  # untouched, retried later
    finally:
        release.set()
        t.join()
    drain(ws.app)
    assert ws.api.get(f"/calculation-runs/{run_id}").json()["status"] == "completed"


def test_failure_mid_run_leaves_an_invisible_partial_run(
    calc: Scenario, admin_engine: Engine, baseline: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = calc.ws
    real, calls = persistence.insert_results, []

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        real(*args, **kwargs)

    monkeypatch.setattr(persistence, "insert_results", flaky)
    r = create_run(ws, **WINDOW)
    assert r.status_code == 202
    run_id = r.json()["run"]["id"]
    drain(ws.app)
    failed = ws.api.get(f"/calculation-runs/{run_id}").json()
    assert failed["status"] == "failed" and failed["totals"] is None
    assert failed["error"]["details"][0]["code"] == "internal_error"
    with admin_engine.connect() as conn:  # the first manufacturer's rows were committed ...
        n = conn.execute(
            text("SELECT count(*) FROM calculation_results WHERE run_id = :r"), {"r": run_id}
        ).scalar_one()
    assert n > 0
    blocked = ws.api.get(f"/calculation-runs/{run_id}/results")  # ... and are invisible
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "invalid_state"
    detail = ws.api.get(f"/calculation-results/{uuid.uuid4()}")
    assert detail.status_code == 404
    with admin_engine.connect() as conn:
        some = conn.execute(
            text("SELECT id FROM calculation_results WHERE run_id = :r LIMIT 1"), {"r": run_id}
        ).scalar_one()
    assert ws.api.get(f"/calculation-results/{some}").status_code == 404  # by id too
    monkeypatch.undo()
    retry = run(ws, **WINDOW)  # a failed run does not block its scope; the next run is complete and identical
    assert retry["status"] == "completed" and retry["totals"]["lines"] == baseline["run"]["totals"]["lines"]


def test_unsupported_rules_and_settings_are_problem_responses_not_500s(calc: Scenario) -> None:
    ws = calc.ws
    brit = ws.manufacturers["BRIT"]
    etag = ws.api.get(f"/manufacturers/{brit}/settings").headers["ETag"]
    gt = next(m for m in truth("master_manufacturers.json") if m["code"] == "BRIT")
    body = {k: v for k, v in gt.items() if k not in ("code", "name")}
    # claim_window_basis = agreement_end is refused when settings are saved (owner decision 2026-11-11)
    bad = put(
        ws.api,
        f"/manufacturers/{brit}/settings",
        {**body, "claim_window_basis": "agreement_end"},
        {"If-Match": etag},
    )
    assert bad.status_code == 422
    # period_end applies to rebates only: saving it is allowed, running SPA lines against it is a clear 422
    ok = put(
        ws.api,
        f"/manufacturers/{brit}/settings",
        {**body, "claim_window_basis": "period_end"},
        {"If-Match": etag},
    )
    assert ok.status_code == 200, ok.text
    refused = create_run(ws, manufacturer_ids=[brit], **WINDOW)
    assert refused.status_code == 422, refused.text
    detail = refused.json()["error"]["details"][0]
    assert detail["code"] == "engine_rejected" and "rebates only" in detail["message"]
    assert (
        create_run(ws, manufacturer_ids=[ws.manufacturers["COPR"]], **WINDOW).status_code == 202
    )  # others unaffected
    drain(ws.app)
    # a setting changed between create and execution fails the run visibly (the engine refuses it again)
    settings(ws, "BRIT", claim_window_basis="ship_date")
    queued = create_run(ws, manufacturer_ids=[brit], **WINDOW)
    assert queued.status_code == 202
    settings(ws, "BRIT", claim_window_basis="period_end")
    drain(ws.app)
    failed = ws.api.get(f"/calculation-runs/{queued.json()['run']['id']}").json()
    assert failed["status"] == "failed" and failed["error"]["details"][0]["code"] == "engine_rejected"
    settings(ws, "BRIT")  # restore ground truth


def test_agreements_with_release_2_rules_cannot_be_activated(calc: Scenario) -> None:
    """cap_scope = per_agreement and tier_metric = quantity are rejected by the validator (the engine refuses them
    too, tests/unit/engine) with the stable code."""
    spa = payload("SPA-VOLT-2026-884")
    spa["header"]["external_number"] = "SPA-R2-CAP"
    spa["rules"]["quantity"]["cap_scope"] = "per_agreement"
    reb = payload("REB-VOLT-2026")
    reb["header"]["external_number"] = "REB-R2-QTY"
    reb["rules"]["tier_metric"] = "quantity"
    for body, field in ((spa, "payload.rules.quantity.cap_scope"), (reb, "payload.rules.tier_metric")):
        draft = create(calc.ws.api, payload=body)
        errors = {(e["field"], e["code"]) for e in draft["validation"]["errors"]}
        assert (field, "not_supported_until_release_2") in errors, errors


# ---- recalculation proposals (S5.3, wired to claim lines in S7) ---------------------------------------------


def test_recalculation_proposals_against_frozen_claims(
    calc: Scenario, admin_engine: Engine, baseline: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = calc.ws
    v = calc.versions["SPA-VOLT-2026-884"]
    ids = line_ids(admin_engine, ws.tenant_id)
    source = FixtureClaims(
        ids,
        v["agreement_id"],
        v["id"],
        uuid.UUID(ws.manufacturers["VOLT"]),
        amounts={"INV-10001/1": "2300.00"},  # claimed 2300.00 but the run says 2200.00
        extra={"INV-10002/1": "500.00"},  # claimed 500.00 on a line that is now price_mismatch
    )
    monkeypatch.setattr(frozen, "source", source)
    rid = baseline["run"]["id"]
    out = ws.api.post(f"/calculation-runs/{rid}/propose-adjustments").json()
    # BUSINESS_LOGIC §9.3 by hand: eligible and different -> new - frozen = 2200.00 - 2300.00 = -100.00;
    # now an exception -> -frozen = -500.00; equal amounts (the other claimed lines) -> no action.
    by_result = {a["calculation_result_id"]: a for a in out["proposed"]}
    first, second = baseline["results"]["INV-10001/1"]["id"], baseline["results"]["INV-10002/1"]["id"]
    assert Decimal(by_result[first]["amount"]) == Decimal("-100.00")
    assert Decimal(by_result[second]["amount"]) == Decimal("-500.00")
    assert len(out["proposed"]) == 2 and out["compared"] >= 2
    assert all(
        a["status"] == "proposed" and a["adjustment_type"] == "agreement_change" for a in out["proposed"]
    )
    again = ws.api.post(f"/calculation-runs/{rid}/propose-adjustments").json()
    assert again["proposed"] == []  # idempotent per (claim line, type, run)
    monkeypatch.setattr(frozen, "source", frozen.NoFrozenClaims())
    none = ws.api.post(f"/calculation-runs/{rid}/propose-adjustments").json()
    assert none == {
        "run_id": rid,
        "compared": 0,
        "proposed": [],
    }  # until S7: no claim lines, nothing to compare


def test_no_floats_in_the_claims_module() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "rebate" / "modules" / "claims"
    offenders = []
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Name) and node.id == "float") or (
                isinstance(node, ast.Constant) and isinstance(node.value, float)
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


# ---- other workspaces ---------------------------------------------------------------------------------------


def _variant(
    env: Env, vid: str, priority_901: int, policy: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    settings(ws, "VOLT", conflict_policy=policy)

    def evergreen(p: dict[str, Any]) -> None:
        p["customers"].append({"customer_code": "EVERG", "scope": "include"})

    def priority(p: dict[str, Any]) -> None:
        p["header"]["priority"] = priority_901

    versions = activate_all(
        ws,
        ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901"),
        {"SPA-VOLT-2026-884": evergreen, "SPA-VOLT-2026-901": priority},
    )
    import_sales(ws)
    r = run(ws, manufacturer_ids=[ws.manufacturers["VOLT"]], **WINDOW)
    assert r["status"] == "completed", r
    variant = next(v for v in CALC["variants"] if v["id"] == vid)
    return variant, results(ws, r["id"]), versions


def test_variant_sc14b_conflicting_agreements_are_flagged(env: Env) -> None:
    variant, res, _ = _variant(env, "SC-14b", 0, "flag")
    r = res[variant["line"]]
    assert (r["status"], r["exception_code"]) == (variant["status"], variant["exception_code"])
    assert (
        Decimal(r["claim_amount"]) == Decimal(variant["claim_amount"]) and r["agreement_version_id"] is None
    )


def test_variant_sc14c_priority_picks_the_winner(env: Env) -> None:
    variant, res, versions = _variant(env, "SC-14c", 10, "priority")
    r = res[variant["line"]]
    assert r["status"] == variant["status"] and Decimal(r["claim_amount"]) == Decimal(variant["claim_amount"])
    assert variant["winner"] == "SPA-VOLT-2026-901"
    assert r["agreement_version_id"] == versions[variant["winner"]]["id"]


def test_volume_file_matches_the_ground_truth_summary(env: Env) -> None:
    gt = truth("expected_volume_summary.json")
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    activate_all(ws, ("SPA-VOLT-2026-884",))
    import_sales(ws, f"sales/{gt['file']}")
    r = run(ws, manufacturer_ids=[ws.manufacturers["VOLT"]], **WINDOW)
    assert r["status"] == "completed", r
    eligible = results(ws, r["id"], status="eligible")
    assert len(eligible) == gt["eligible_count"]
    assert sum((Decimal(x["claim_amount"]) for x in eligible.values()), Decimal(0)) == Decimal(
        gt["eligible_total"]
    )
    assert Decimal(r["totals"]["eligible_amount"]) == Decimal(gt["eligible_total"])
    assert r["totals"]["by_exception"] == gt["exceptions"]


def test_claim_window_month_end_deadline_is_inclusive(env: Env) -> None:
    """BUSINESS_LOGIC §12 Example D3: invoice 2026-03-10, month_end, 60 days -> month end 2026-03-31, deadline
    2026-05-30; eligible on 2026-05-30, outside_claim_window on 2026-05-31 (amount kept). 10 x (100.00 - 78.00)."""
    before = env.clock.t
    try:
        env.clock.t = datetime(2026, 5, 31, 4, 50, tzinfo=UTC)  # 23:50 on 2026-05-30 in America/Chicago
        ws = open_workspace(env.app, env.stack)
        settings(ws, "VOLT", claim_window_basis="month_end", claim_window_days=60)
        activate_all(ws, ("SPA-VOLT-2026-884",))
        header = data(SCENARIOS).decode().splitlines()[0]
        row = "INV-D3-1,1,2026-03-10,2026-03-10,ALDER,Alder Electric LLC (SYNTHETIC),BR01,BRK-3P-100A,Breaker 3-pole 100A,10,EA,88.00,100.00,880.00,SPA-VOLT-2026-884,,INV,,"
        run_import(ws, clean_file(ws, "d3.csv", f"{header}\n{row}\n".encode()), "sales")
        scope = {"manufacturer_ids": [ws.manufacturers["VOLT"]], **WINDOW}
        last_day = run(ws, **scope)
        (r,) = results(ws, last_day["id"]).values()
        assert (r["status"], Decimal(r["claim_amount"])) == ("eligible", Decimal("220.00"))
        trace = ws.api.get(f"/calculation-results/{r['id']}").json()["trace"]
        window = next(s for s in trace["steps"] if s["step"] == "claim_window")
        assert (window["basis"], window["month_end"], window["deadline"]) == (
            "month_end",
            "2026-03-31",
            "2026-05-30",
        )
        target = datetime(2026, 5, 31, 5, 10, tzinfo=UTC)  # 00:10 on 2026-05-31 in America/Chicago
        while env.clock.t < target:  # step in short hops: a long jump would idle the session out
            env.clock.advance(600)
            assert ws.api.get("/auth/me").status_code == 200
        late = run(ws, **scope)
        (r,) = results(ws, late["id"]).values()
        assert (r["status"], r["exception_code"]) == ("exception", "outside_claim_window")
        assert Decimal(r["claim_amount"]) == Decimal("220.00")  # money at risk stays visible
        assert Decimal(late["totals"]["at_risk_amount"]) == Decimal("220.00")
    finally:
        env.clock.t = before


def test_rebate_ledger_from_purchases_and_sales_basis(env: Env) -> None:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    versions = activate_all(ws, ("REB-VOLT-2026", "REB-NWND-2026"))
    import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")
    nwnd_file = truth("expected_rebates_nwnd.json")["file"]
    import_sales(ws, nwnd_file)
    volt = {"manufacturer_ids": [ws.manufacturers["VOLT"]], "date_from": "2026-01-01"}
    gt = truth("expected_rebates.json")
    agreement = versions[gt["agreement"]]["agreement_id"]
    for case in gt["cases"]:
        r = run(ws, date_to=case["as_of"], **volt)
        assert r["status"] == "completed", r
        (e,) = ws.api.get("/rebate-accruals", params={"run_id": r["id"]}).json()["data"]
        assert e["agreement_id"] == agreement and e["as_of_date"] == case["as_of"]
        assert Decimal(e["cumulative_qualifying"]) == Decimal(case["cumulative_qualifying"]), case["id"]
        assert e["tier_number"] == case["tier"], case["id"]
        assert Decimal(e["cumulative_earned"]) == Decimal(case["earned_cumulative"]), case["id"]
        assert Decimal(e["prior_cumulative_accrued"]) == Decimal(case["prior_accrued"]), case["id"]
        assert Decimal(e["delta"]) == Decimal(case["delta"]), case["id"]
        assert ("rebate_clawback" in e["flags"]) == (Decimal(case["delta"]) < 0), case["id"]
        assert e["claim_deadline"] == "2027-03-01"  # period end 2026-12-31 + 60 days (hand: 31 + 28 + 1)
        again = run(
            ws, date_to=case["as_of"], **volt
        )  # unchanged data: the ledger delta is 0.00 (gt idempotency)
        (e2,) = ws.api.get("/rebate-accruals", params={"run_id": again["id"]}).json()["data"]
        assert Decimal(e2["delta"]) == Decimal("0.00") and Decimal(e2["prior_cumulative_accrued"]) == Decimal(
            case["earned_cumulative"]
        )
        assert Decimal(again["totals"]["accrual_delta"]) == 0
    ledger = ws.api.get("/rebate-accruals", params={"agreement_id": agreement, "limit": 200}).json()["data"]
    assert sum((Decimal(x["delta"]) for x in ledger), Decimal(0)) == Decimal(
        "14850.00"
    )  # deltas sum to the cumulative
    tracker = {t["external_number"]: t for t in ws.api.get("/rebate-tracker").json()["data"]}
    t = tracker[gt["agreement"]]
    # after GL-R2: volume 990,000.00, tier 1 (1.5%), next tier at 1,000,000 -> 10,000.00 to go, accrued 14,850.00
    assert (t["tier_number"], Decimal(t["tier_rate"])) == (1, Decimal("0.015"))
    assert Decimal(t["next_tier_lower_bound"]) == 1000000 and Decimal(t["amount_to_next_tier"]) == Decimal(
        "10000"
    )
    assert Decimal(t["accrued_to_date"]) == Decimal("14850.00") and [
        x["tier_number"] for x in t["tiers"]
    ] == [1, 2, 3]

    nw = truth("expected_rebates_nwnd.json")
    case = nw["cases"][0]
    r = run(ws, manufacturer_ids=[ws.manufacturers["NWND"]], date_from="2026-01-01", date_to=case["as_of"])
    assert r["status"] == "completed", r
    (e,) = ws.api.get("/rebate-accruals", params={"run_id": r["id"]}).json()["data"]
    assert Decimal(e["cumulative_qualifying"]) == Decimal(case["cumulative_qualifying"])
    assert Decimal(e["cumulative_payout_base"]) == Decimal(case["payout_base"])
    assert e["tier_number"] == case["tier"] and Decimal(e["cumulative_earned"]) == Decimal(
        case["earned_cumulative"]
    )
    # SPA-line identification (5.4) needs the SPA results of every sales line of the period: a run that starts later
    # than the rebate period is refused up front
    narrow = create_run(
        ws, manufacturer_ids=[ws.manufacturers["NWND"]], date_from="2026-02-01", date_to=case["as_of"]
    )
    assert narrow.status_code == 422 and narrow.json()["error"]["details"][0]["code"] == "scope_too_narrow"


def test_preview_calculation_runs_the_engine_on_a_draft_and_persists_nothing(
    env: Env, admin_engine: Engine
) -> None:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    import_sales(ws)
    import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")
    spa = create(ws.api, payload=payload("SPA-VOLT-2026-884"))
    out = ws.api.post(f"/agreement-drafts/{spa['id']}/preview-calculation", json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["kind"] == "spa" and body["persisted"] is False and body["run_date"] == "2026-07-10"
    lines = {f"{x['document_number']}/{x['line_number']}": x for x in body["lines"]}
    by_id = {f"{s['invoice_no']}/{s['line_no']}": s for s in CALC["scenarios"]}
    for key in (
        "INV-10001/1",
        "INV-10002/1",
        "INV-10005/1",
        "INV-10006/1",
    ):  # SC-01, 02, 05, 06 (ground truth)
        sc = by_id[key]
        assert lines[key]["status"] == sc["status"], key
        assert lines[key]["exception_code"] == sc.get("exception_code"), key
        assert Decimal(lines[key]["claim_amount"]) == Decimal(sc.get("claim_amount", "0")), key
    assert lines["INV-10001/1"]["trace"]["result"]["claim_amount"] == "2200.00"
    assert body["sample_size"] == len(lines) <= 200
    limited = ws.api.post(f"/agreement-drafts/{spa['id']}/preview-calculation", json={"limit": 3}).json()
    assert limited["sample_size"] == 3
    assert (
        ws.api.post(f"/agreement-drafts/{spa['id']}/preview-calculation", json={"limit": 201}).status_code
        == 422
    )

    # rebate draft: cumulative purchases to the business date, tiers by hand (BUSINESS_LOGIC 5.1/5.2)
    reb = create(ws.api, payload=payload("REB-VOLT-2026"))
    r = ws.api.post(f"/agreement-drafts/{reb['id']}/preview-calculation", json={})
    assert r.status_code == 200, r.text
    acc = r.json()["accrual"]
    rows = [x for x in data("purchases/purchases_VOLT_2026.csv").decode().splitlines()[1:]]
    volume = sum((Decimal(x.split(",")[10]) for x in rows if x.split(",")[2] <= "2026-07-10"), Decimal(0))
    tier, rate = max(
        (
            (n, rt)
            for n, lo, rt in ((1, 0, "0.015"), (2, 1000000, "0.025"), (3, 2000000, "0.035"))
            if lo <= volume
        )
    )
    assert Decimal(acc["cumulative_qualifying"]) == volume and acc["tier_number"] == tier
    assert Decimal(acc["cumulative_earned"]) == (volume * Decimal(rate)).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )

    bad = payload("SPA-VOLT-2026-884")
    bad["rules"]["quantity"]["cap_scope"] = "per_agreement"
    bad["header"]["external_number"] = "SPA-PREVIEW-R2"
    refused = ws.api.post(
        f"/agreement-drafts/{create(ws.api, payload=bad)['id']}/preview-calculation", json={}
    )
    assert refused.status_code == 422, refused.text
    assert "not_supported_until_release_2" in {d["code"] for d in refused.json()["error"]["details"]}
    assert ws.api.post(f"/agreement-drafts/{uuid.uuid4()}/preview-calculation", json={}).status_code == 404

    with admin_engine.connect() as conn:  # nothing was persisted
        for table in (
            "calculation_runs",
            "calculation_results",
            "rebate_accrual_entries",
            "claim_adjustments",
        ):
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": ws.tenant_id}
            ).scalar_one()
            assert n == 0, table
