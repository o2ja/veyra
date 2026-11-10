"""Crash/resume and job-lease safety of the chunked loader (S2 report requirement 1, S3.6), on real
Postgres: a crashed chunk rolls back and resumes; a chunk that outlives its lease is reclaimed by a second
worker and still no row is loaded twice; lease renewal keeps a long job its own."""

import threading
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebate.core import jobs
from rebate.core.db import transaction
from rebate.core.jobs import JobContext
from rebate.modules.ingestion import loader, service
from tests.integration.conftest import Workspace, clean_file, map_suggested, start
from tests.support import FakeClock, drain
from tests.testdata import data


def _confirmed(ws: Workspace) -> str:
    batch = start(ws, clean_file(ws, "a.csv", data("sales/sales_scenarios_layoutA.csv")), "sales")
    batch = map_suggested(ws, batch)
    r = ws.api.post(f"/imports/{batch['id']}/confirm", json={})
    assert r.status_code == 202, r.text
    return str(batch["id"])


def _assert_loaded_once(engine: Engine, tenant: str, batch_id: str) -> None:
    with engine.connect() as conn:
        total, keys = conn.execute(
            text(
                "SELECT count(*), count(DISTINCT (document_number, line_number)) FROM sales_document_lines "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant},
        ).one()
        status, counts = conn.execute(
            text("SELECT status, counts FROM import_batches WHERE id = :b"), {"b": batch_id}
        ).one()
    assert total == keys == 23
    assert status == "completed" and counts["inserted"] == 23 and counts["unchanged"] == 0


def test_crash_mid_load_rolls_back_the_chunk_and_resumes(
    ws: Workspace, clk: FakeClock, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "LOAD_CHUNK", 5)
    real = loader.load_sales
    calls = {"n": 0}

    def crashing(db: Session, batch: Any, rows: Any, counts: Any) -> None:
        calls["n"] += 1
        real(db, batch, rows, counts)  # rows are written ...
        if calls["n"] == 2:
            raise RuntimeError("worker killed mid-chunk")  # ... and lost with the transaction

    monkeypatch.setattr(loader, "load_sales", crashing)
    batch_id = _confirmed(ws)
    drain(ws.app)
    with admin_engine.connect() as conn:
        loaded = conn.execute(
            text("SELECT count(*) FROM sales_document_lines WHERE tenant_id = :t"), {"t": ws.tenant_id}
        ).scalar_one()
        cp = conn.execute(
            text("SELECT checkpoint FROM import_batches WHERE id = :b"), {"b": batch_id}
        ).scalar_one()
    assert loaded == 5 and cp["chunk"] == 1  # only the committed first chunk exists
    monkeypatch.setattr(loader, "load_sales", real)
    clk.advance(3600)  # past the retry backoff
    drain(ws.app)
    _assert_loaded_once(admin_engine, ws.tenant_id, batch_id)


def _job_row(engine: Engine, batch_id: str) -> Any:
    with engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT status, attempts FROM jobs WHERE dedupe_key = :k"),
                {"k": f"import_load:{batch_id}:0"},
            )
            .mappings()
            .one()
        )


def _wait(predicate: Any, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


def test_chunk_outliving_its_lease_is_reclaimed_without_double_loading(
    ws: Workspace, clk: FakeClock, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "LEASE", timedelta(seconds=30))
    batch_id = _confirmed(ws)
    engine, settings = ws.app.state.engine, ws.app.state.settings
    blocker = admin_engine.connect()
    tx = blocker.begin()
    blocker.execute(text("LOCK TABLE sales_document_lines IN SHARE MODE"))  # worker 1 stalls on its inserts
    try:
        first = threading.Thread(target=jobs.run_once, args=(engine, settings))
        first.start()
        _wait(lambda: _job_row(admin_engine, batch_id)["status"] == "running")
        time.sleep(0.5)  # worker 1 holds the batch lock and waits on the table lock
        clk.advance(60)  # its lease expires
        second = threading.Thread(target=jobs.run_once, args=(engine, settings))
        second.start()
        _wait(lambda: _job_row(admin_engine, batch_id)["attempts"] == 2)  # reclaimed by worker 2
    finally:
        tx.commit()  # release: worker 1 finishes its writes, loses the lease, rolls back
        blocker.close()
    first.join(30)
    second.join(30)
    assert not first.is_alive() and not second.is_alive()
    assert _job_row(admin_engine, batch_id)["status"] == "succeeded"
    drain(ws.app)
    _assert_loaded_once(admin_engine, ws.tenant_id, batch_id)


_seen: dict[str, Any] = {}


@jobs.job("test_lease_renewal", tenant_scoped=False, max_attempts=1)
def _long_job(db: Session, ctx: JobContext) -> None:
    clock: FakeClock = _seen["clock"]
    clock.advance(25)
    ctx.renew_lease()
    clock.advance(25)  # 50 s after the claim: past the original 30 s lease, inside the renewed one
    _seen["reclaimed"] = jobs._claim(ctx.engine)  # type: ignore[arg-type]
    if _seen.get("steal"):
        with ctx.engine.begin() as conn:  # type: ignore[union-attr]
            conn.execute(
                text(
                    "UPDATE jobs SET attempts = attempts + 1, max_attempts = max_attempts + 1 WHERE id = :i"
                ),
                {"i": ctx.id},
            )
        ctx.renew_lease()  # another worker owns it now


def test_lease_renewal_keeps_the_job_and_detects_a_takeover(
    ws: Workspace, clk: FakeClock, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "LEASE", timedelta(seconds=30))
    engine, settings = ws.app.state.engine, ws.app.state.settings
    _seen.clear()
    _seen["clock"] = clk
    for steal in (False, True):
        _seen["steal"] = steal
        with transaction(engine) as db:
            job_id = jobs.enqueue(
                db, "test_lease_renewal", tenant_id=None, payload={}, dedupe_key=f"t:{uuid.uuid4()}"
            )
        assert jobs.run_once(engine, settings)
        assert _seen["reclaimed"] is None  # nobody could claim the renewed job
        with admin_engine.connect() as conn:
            status = conn.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": job_id}).scalar_one()
        assert status == ("running" if steal else "succeeded")  # LeaseLost: this run's outcome is discarded
    with pytest.raises(RuntimeError):
        JobContext(settings, uuid.uuid4(), None, {}, 1, None).renew_lease()
