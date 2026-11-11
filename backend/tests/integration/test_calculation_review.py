"""Review findings of the S5 financial/DB review (F1, F2, F4, F7): split-period terms fail closed, job recovery,
claim-window date from today, and the data update of the migration."""

import importlib
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from rebate.modules.claims import runs
from tests.conftest import DbUrls
from tests.integration.agreements_support import approve, create, submit, update
from tests.integration.calc_support import (
    activate_all,
    all_settings,
    create_run,
    open_workspace,
    results,
    run,
    settings,
)
from tests.integration.conftest import Workspace, clean_file, import_file, run_import
from tests.integration.test_calculation_runs import NOW, WINDOW, Env
from tests.support import drain, fake_clock, make_app
from tests.testdata import data


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


def _amend(ws: Workspace, version: dict[str, Any], valid_from: str, rate: str | None) -> dict[str, Any]:
    """Amend a rebate version from ``valid_from`` (``rate``: new tier-1 rate, None = identical terms)."""
    d = create(ws.api, amends_version_id=version["id"])
    p = d["payload"]
    p["header"]["valid_from"] = valid_from
    if rate is not None:
        p["tiers"][0]["rate"] = rate
    d = update(ws.api, d, p).json()
    assert d["validation"]["valid"], d["validation"]
    assert submit(ws.api, d["id"], [w["id"] for w in d["validation"]["warnings"]]).status_code == 200
    r = approve(ws.api, d["id"])
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_split_period_with_different_terms_fails_closed(env: Env) -> None:
    """Owner decision pending: how tiers apply across a mid-period amendment. Identical terms (same content hash)
    are unambiguous and run; different terms are refused with `split_period_terms_need_decision`."""
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    base = activate_all(ws, ("REB-VOLT-2026",))["REB-VOLT-2026"]
    import_file(ws, "purchases/purchases_VOLT_2026.csv", "purchases")
    volt = {
        "manufacturer_ids": [ws.manufacturers["VOLT"]],
        "date_from": "2026-01-01",
        "date_to": "2026-08-30",
    }
    _amend(ws, base, "2026-07-01", None)  # same terms, split at 07-01
    same = run(ws, **volt)
    assert same["status"] == "completed", same
    (entry,) = ws.api.get("/rebate-accruals", params={"run_id": same["id"]}).json()["data"]
    assert Decimal(entry["cumulative_earned"]) == Decimal(
        "14850.00"
    )  # 990,000 x 1.5% (expected_rebates.json GL-R1a)

    versions = ws.api.get(f"/agreements/{base['agreement_id']}/versions").json()["data"]
    v2 = max((v for v in versions if v["status"] == "active"), key=lambda v: v["valid_from"])
    _amend(
        ws, v2, "2026-10-01", "0.016"
    )  # different terms from 10-01: the period is now covered by two term sets
    refused = create_run(ws, **{**volt, "date_to": "2026-12-31"})
    assert refused.status_code == 422, refused.text
    d = refused.json()["error"]["details"][0]
    assert d["code"] == "split_period_terms_need_decision" and "REB-VOLT-2026" in d["field"]
    assert d["message"].count("version ") >= 2 and "owner decision" in d["message"]
    # a run that ends before the differing version starts is still unambiguous
    assert create_run(ws, **{**volt, "date_to": "2026-09-30"}).status_code == 202
    drain(ws.app)


def test_claim_window_uses_today_not_a_backdated_as_of(env: Env) -> None:
    """F4: `as_of` pins which agreement versions are visible; the claim window is judged on today's business date."""
    before = env.clock.t
    try:
        env.clock.t = datetime(2026, 5, 31, 4, 50, tzinfo=UTC)  # 23:50 on 2026-05-30 in America/Chicago
        ws = open_workspace(env.app, env.stack)
        settings(ws, "VOLT", claim_window_basis="month_end", claim_window_days=60)
        activate_all(ws, ("SPA-VOLT-2026-884",))
        header = data("sales/sales_scenarios_layoutA.csv").decode().splitlines()[0]
        row = "INV-F4-1,1,2026-03-10,2026-03-10,ALDER,Alder Electric LLC (SYNTHETIC),BR01,BRK-3P-100A,Breaker 3-pole 100A,10,EA,88.00,100.00,880.00,SPA-VOLT-2026-884,,INV,,"
        run_import(ws, clean_file(ws, "f4.csv", f"{header}\n{row}\n".encode()), "sales")
        while env.clock.t < datetime(
            2026, 5, 31, 5, 10, tzinfo=UTC
        ):  # today is now 2026-05-31 (deadline 2026-05-30)
            env.clock.advance(600)
            assert ws.api.get("/auth/me").status_code == 200
        r = run(ws, manufacturer_ids=[ws.manufacturers["VOLT"]], as_of="2026-05-31T04:55:00+00:00", **WINDOW)
        assert r["status"] == "completed" and r["run_date"] == "2026-05-31", r
        (res,) = results(ws, r["id"]).values()
        assert (res["status"], res["exception_code"]) == ("exception", "outside_claim_window")
    finally:
        env.clock.t = before


def _hold_lock(admin: Engine, tenant_id: str) -> tuple[threading.Event, threading.Thread]:
    holder, release = threading.Event(), threading.Event()

    def hold() -> None:
        with admin.connect() as conn, conn.begin():
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": runs.LOCK_KEY.format(tenant=tenant_id)},
            )
            holder.set()
            release.wait(60)

    t = threading.Thread(target=hold)
    t.start()
    assert holder.wait(10)
    return release, t


def _jobs_of(admin: Engine, run_id: str) -> list[Any]:
    with admin.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT status, attempts, run_at FROM jobs WHERE kind = 'calculation_run' "
                    "AND payload->>'run_id' = :r ORDER BY created_at"
                ),
                {"r": run_id},
            )
        )


def test_waiting_for_the_lock_burns_no_attempts_and_the_run_completes(env: Env, admin_engine: Engine) -> None:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    activate_all(ws, ("SPA-VOLT-2026-884",))
    r = create_run(ws, manufacturer_ids=[ws.manufacturers["VOLT"]], **WINDOW).json()["run"]
    release, t = _hold_lock(admin_engine, ws.tenant_id)
    try:
        for _ in range(3):  # three waits in a row: with the old retry-with-backoff the job would be spent
            drain(ws.app)
            env.clock.advance(31)
        rows = _jobs_of(admin_engine, r["id"])
        assert [x.status for x in rows].count("failed") == 0
        assert all(x.attempts <= 1 for x in rows) and len(rows) >= 3
        assert ws.api.get(f"/calculation-runs/{r['id']}").json()["status"] == "queued"
    finally:
        release.set()
        t.join()
    env.clock.advance(31)
    drain(ws.app)
    assert ws.api.get(f"/calculation-runs/{r['id']}").json()["status"] == "completed"


def test_dead_job_fails_the_run_so_the_scope_is_free_again(
    env: Env,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = env.clock.t
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    activate_all(ws, ("SPA-VOLT-2026-884",))
    body = {"manufacturer_ids": [ws.manufacturers["VOLT"]], **WINDOW}
    r = create_run(ws, **body).json()["run"]

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("worker crashed")

    monkeypatch.setattr(runs, "execute", boom)
    try:
        for _ in range(6):  # max_attempts = 5, exponential backoff
            env.clock.advance(1000)
            drain(ws.app)
    finally:
        env.clock.t = before
    (job,) = _jobs_of(admin_engine, r["id"])
    assert job.status == "failed"
    with admin_engine.connect() as conn:
        status, error = conn.execute(
            text("SELECT status, error FROM calculation_runs WHERE id = :r"), {"r": r["id"]}
        ).one()
    assert status == "failed" and error["details"][0]["code"] == "job_failed"
    monkeypatch.undo()
    assert run(ws, **body)["status"] == "completed"  # the scope is not blocked


def test_run_abandoned_by_a_dead_worker_is_failed_by_the_next_attempt(env: Env, admin_engine: Engine) -> None:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    activate_all(ws, ("SPA-VOLT-2026-884",))
    r = create_run(ws, manufacturer_ids=[ws.manufacturers["VOLT"]], **WINDOW).json()["run"]
    with admin_engine.begin() as conn:  # the first attempt got as far as `running`, then its worker died
        conn.execute(
            text("UPDATE calculation_runs SET status = 'running', started_at = now() WHERE id = :r"),
            {"r": r["id"]},
        )
    drain(ws.app)
    with admin_engine.connect() as conn:
        status, error = conn.execute(
            text("SELECT status, error FROM calculation_runs WHERE id = :r"), {"r": r["id"]}
        ).one()
    assert status == "failed" and error["details"][0]["code"] == "stale_run"
    assert (
        create_run(ws, manufacturer_ids=[ws.manufacturers["VOLT"]], **WINDOW).status_code == 202
    )  # scope free
    drain(ws.app)


def test_migration_turns_period_end_settings_into_month_end(
    env: Env, admin_engine: Engine, owner_engine: Engine
) -> None:
    """F7: the old `period_end` meant the month end of the claim date; the migration keeps that meaning. It runs as the
    owner role, which is subject to FORCE RLS: the statement sequence of the migration must still reach the rows."""
    migration = importlib.import_module("migrations.versions.20261114_0900_calc_runs")
    ws = open_workspace(env.app, env.stack)
    volt = ws.manufacturers["VOLT"]
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE manufacturer_settings SET claim_window_basis = 'period_end', claim_window_days = 60 "
                "WHERE manufacturer_id = :m"
            ),
            {"m": volt},
        )
    read = text("SELECT claim_window_basis, revision FROM manufacturer_settings WHERE manufacturer_id = :m")
    with admin_engine.connect() as conn:
        before_basis, before_revision = conn.execute(read, {"m": volt}).one()
    assert before_basis == "period_end"
    with (
        owner_engine.begin() as conn
    ):  # the bare statement matches nothing (no tenant context, FORCE RLS) ...
        assert conn.execute(text(migration.PERIOD_END_TO_MONTH_END)).rowcount == 0
    with owner_engine.begin() as conn:  # ... the migration's sequence matches the rows
        for statement in migration.PERIOD_END_STEPS:
            conn.execute(text(statement))
    with admin_engine.connect() as conn:
        assert tuple(conn.execute(read, {"m": volt}).one()) == ("month_end", before_revision + 1)
        forced = conn.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'manufacturer_settings'")
        ).scalar_one()
    assert forced is True  # FORCE is back on
