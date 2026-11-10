"""Job queue (ADR-005 fallback, ARCHITECTURE.md §6): transactional enqueue, retry with backoff,
dead letter, idempotency, tenant context, lease reclaim; admin API; K-9 replacement; token decision."""

from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebate.core import jobs
from rebate.core.db import transaction
from rebate.core.jobs import JobContext, PermanentError
from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.security import samples
from tests.security.flows import full_admin, login
from tests.support import (
    STRONG_PASSWORD,
    Api,
    FakeClock,
    api_client,
    drain,
    make_app,
    make_settings,
    unique_email,
)

_calls: dict[str, list[Any]] = {}


@jobs.job("test_flaky", tenant_scoped=False, max_attempts=3)
def _flaky(db: Session, ctx: JobContext) -> None:
    _calls.setdefault(str(ctx.id), []).append(ctx.attempt)
    if ctx.attempt <= int(ctx.payload["fail_times"]):
        raise RuntimeError("secret detail that must not be stored")


@jobs.job("test_permanent", tenant_scoped=False)
def _permanent(db: Session, ctx: JobContext) -> None:
    raise PermanentError("bad input")


@jobs.job("test_tenant_probe", tenant_scoped=True)
def _probe(db: Session, ctx: JobContext) -> None:
    seen = db.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar_one()
    _calls.setdefault(str(ctx.id), []).append(seen)


def _row(engine: Engine, job_id: UUID | str) -> Any:
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM jobs WHERE id = :i"), {"i": str(job_id)}).mappings().one()


def _enqueue(app: FastAPI, kind: str, tenant_id: UUID | None = None, **payload: str) -> UUID:
    with transaction(app.state.engine) as db:
        job_id = jobs.enqueue(db, kind, tenant_id=tenant_id, payload=payload)
    assert job_id is not None
    return job_id


def _run_all_due(app: FastAPI) -> None:
    while jobs.run_once(app.state.engine, app.state.settings):
        pass


def test_retry_with_backoff_then_success(app: FastAPI, clk: FakeClock, admin_engine: Engine) -> None:
    job_id = _enqueue(app, "test_flaky", fail_times="2")
    _run_all_due(app)
    first = _row(admin_engine, job_id)
    assert first["status"] == "queued" and first["attempts"] == 1
    assert first["last_error"] == "RuntimeError"  # class only: exception text never stored
    for _ in range(2):
        clk.advance(3600)
        _run_all_due(app)
    done = _row(admin_engine, job_id)
    assert done["status"] == "succeeded" and done["attempts"] == 3 and done["finished_at"]
    assert _calls[str(job_id)] == [1, 2, 3]


def test_backoff_grows_exponentially_with_jitter() -> None:
    delays = [jobs.backoff(n).total_seconds() for n in (1, 2, 3, 4)]
    for n, d in enumerate(delays, start=1):
        base = jobs.BASE_BACKOFF_SECONDS * 2 ** (n - 1)
        assert 0.8 * base <= d <= 1.2 * base
    assert jobs.backoff(30).total_seconds() <= jobs.MAX_BACKOFF_SECONDS * 1.2


def test_dead_letter_after_max_attempts_and_permanent_errors(
    app: FastAPI, clk: FakeClock, admin_engine: Engine
) -> None:
    always = _enqueue(app, "test_flaky", fail_times="99")
    for _ in range(5):
        _run_all_due(app)
        clk.advance(3600)
    dead = _row(admin_engine, always)
    assert dead["status"] == "failed" and dead["attempts"] == 3 and dead["finished_at"]
    permanent = _enqueue(app, "test_permanent")
    _run_all_due(app)
    p = _row(admin_engine, permanent)
    assert p["status"] == "failed" and p["attempts"] == 1 and p["last_error"] == "PermanentError: bad input"


def test_dedupe_key_makes_enqueue_idempotent(app: FastAPI, admin_engine: Engine) -> None:
    key = f"test:{unique_email('dedupe')}"
    with transaction(app.state.engine) as db:
        first = jobs.enqueue(db, "test_flaky", tenant_id=None, payload={"fail_times": "0"}, dedupe_key=key)
        second = jobs.enqueue(db, "test_flaky", tenant_id=None, payload={"fail_times": "0"}, dedupe_key=key)
    assert first is not None and second is None
    with admin_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM jobs WHERE dedupe_key = :k"), {"k": key}).scalar_one()
            == 1
        )


def test_enqueue_is_transactional(app: FastAPI, admin_engine: Engine) -> None:
    class Abort(Exception):
        pass

    with pytest.raises(Abort), transaction(app.state.engine) as db:
        job_id = jobs.enqueue(db, "test_flaky", tenant_id=None, payload={"fail_times": "0"})
        raise Abort
    with admin_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs WHERE id = :i"), {"i": job_id}).scalar_one() == 0


def test_tenant_context_is_set_and_required(app: FastAPI, api: Api, admin_engine: Engine) -> None:
    full_admin(api, unique_email("probe"))
    tenant = UUID(api.get("/auth/me").json()["tenant"]["id"])
    ok = _enqueue(app, "test_tenant_probe", tenant)
    with pytest.raises(ValueError, match="tenant-scoped"):
        _enqueue(app, "test_tenant_probe", None)
    with admin_engine.begin() as conn:  # bypass enqueue() to simulate a job that lost its tenant
        orphan = conn.execute(
            text(
                "INSERT INTO jobs (id, kind, payload) VALUES (gen_random_uuid(), 'test_tenant_probe', '{}') RETURNING id"
            )
        ).scalar_one()
    _run_all_due(app)
    assert _calls[str(ok)] == [str(tenant)]
    failed = _row(admin_engine, orphan)
    assert failed["status"] == "failed" and "without tenant context" in failed["last_error"]
    assert str(orphan) not in _calls  # the handler never ran


def test_expired_lease_is_reclaimed(app: FastAPI, clk: FakeClock, admin_engine: Engine) -> None:
    job_id = _enqueue(app, "test_flaky", fail_times="0")
    with admin_engine.begin() as conn:  # a worker crashed mid-job
        conn.execute(
            text("UPDATE jobs SET status = 'running', attempts = 1, locked_until = :t WHERE id = :i"),
            {"t": clk.now(), "i": job_id},
        )
    clk.advance(1)
    _run_all_due(app)
    assert _row(admin_engine, job_id)["status"] == "succeeded"


def test_email_jobs_carry_ids_only_and_mint_tokens_in_the_job(
    app: FastAPI, api: Api, admin_engine: Engine
) -> None:
    email = unique_email("tokenjob")
    api.post(
        "/auth/register",
        json={"email": email, "full_name": "T", "workspace_name": "T", "password": STRONG_PASSWORD},
    )
    with admin_engine.connect() as conn:
        pending = conn.execute(
            text("SELECT id, token_hash FROM pending_registrations WHERE email = :e"), {"e": email}
        ).one()
        job = conn.execute(
            text(
                "SELECT payload FROM jobs WHERE kind = 'identity_registration_email' AND payload->>'pending_id' = :p"
            ),
            {"p": str(pending.id)},
        ).scalar_one()
    assert pending.token_hash is None  # nothing minted on the request path
    assert set(job) == {"pending_id"}  # no email, no token in the payload
    drain(app)
    with admin_engine.connect() as conn:
        minted = conn.execute(
            text("SELECT token_hash FROM pending_registrations WHERE id = :i"), {"i": pending.id}
        ).scalar_one()
    assert minted is not None and len(minted) == 64
    forgot_email = unique_email("forgotjob")
    api.post("/auth/password/forgot", json={"email": forgot_email})
    with admin_engine.connect() as conn:
        payload = conn.execute(
            text(
                "SELECT payload FROM jobs WHERE kind = 'identity_password_reset' ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert set(payload) == {"request_id"} and forgot_email not in str(payload)


def test_scan_job_with_another_tenants_file_fails(
    app: FastAPI, api: Api, api2: Api, admin_engine: Engine
) -> None:
    storage.ensure_bucket(app.state.settings)
    full_admin(api, unique_email("scanA"))
    full_admin(api2, unique_email("scanB"))
    csrf = api2.csrf()
    b_file = api2.client.post(
        "/api/v1/files",
        files={"file": ("b.csv", samples.CSV + b"b,1\n", "text/csv")},
        data={"kind": "attachment"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
    ).json()["id"]
    tenant_a = UUID(api.get("/auth/me").json()["tenant"]["id"])
    forged = _enqueue(app, "files_scan", tenant_a, file_id=b_file)
    _run_all_due(app)
    row = _row(admin_engine, forged)
    assert row["status"] == "failed" and "not found" in row["last_error"]
    with admin_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT scan_status FROM file_objects WHERE id = :i"), {"i": b_file}
            ).scalar_one()
            == "clean"
        )


def test_scanner_outage_dead_letters_then_admin_retry_succeeds(
    migrated_db: DbUrls, clk: FakeClock, admin_engine: Engine
) -> None:
    broken = make_app(migrated_db.app, clamd_host="127.0.0.1", clamd_port=9)  # nothing listens
    storage.ensure_bucket(make_settings(migrated_db.app))
    with api_client(broken) as api:
        account = full_admin(api, unique_email("outage"))
        csrf = api.csrf()
        file_id = api.client.post(
            "/api/v1/files",
            files={"file": ("o.csv", samples.CSV + b"o,1\n", "text/csv")},
            data={"kind": "attachment"},
            headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        ).json()["id"]
        for _ in range(6):
            drain(broken)
            clk.advance(4000)
        login(api, account)  # the fake clock moved hours ahead: the earlier session has expired
        assert api.get(f"/files/{file_id}").json()["scan_status"] == "error"
        failed = api.get("/jobs", params={"status": "failed"}).json()["data"]
        scan_job = next(j for j in failed if j["kind"] == "files_scan")
        assert scan_job["attempts"] == 5 and scan_job["last_error"] == "ScannerError"
        r = api.post(f"/jobs/{scan_job['id']}/retry")
        assert r.status_code == 200 and r.json()["status"] == "queued"
        assert api.post(f"/jobs/{scan_job['id']}/retry").status_code == 409  # only failed jobs
        actions = [
            e["action"] for e in api.get("/audit-log", params={"action": "job.retried"}).json()["data"]
        ]
        assert actions == ["job.retried"]
    fixed = make_app(migrated_db.app)  # clamd reachable again
    drain(fixed)
    with admin_engine.connect() as conn:
        status = conn.execute(
            text("SELECT scan_status FROM file_objects WHERE id = :i"), {"i": file_id}
        ).scalar_one()
    assert status == "clean"
