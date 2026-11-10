"""Regression tests for the S2 reviewer findings: queue lease safety, dead-letter hooks, scan error
notification, re-upload supersede, scanner limits, XLSX active content, upload gate before the body."""

import io
import os
import socket
import threading
import zipfile
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from rebate.core import jobs
from rebate.core.db import transaction
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError
from rebate.modules.files import scanner, storage
from rebate.modules.files.validation import UploadRejected, _check_xlsx, sanitise_filename
from tests.conftest import DbUrls
from tests.security import samples
from tests.security import test_jobs as _registers_test_jobs  # noqa: F401  (test_flaky, test_tenant_probe)
from tests.security.flows import full_admin, login
from tests.support import (
    Api,
    FakeClock,
    api_client,
    drain,
    make_app,
    make_settings,
    messages_to,
    unique_email,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_seen: dict[str, Any] = {}


def _broken_hook(db: Session, ctx: JobContext) -> None:
    raise RuntimeError("hook bug")


@jobs.job("test_hook_fails", tenant_scoped=False, on_dead=_broken_hook)
def _hook_fails(db: Session, ctx: JobContext) -> None:
    raise PermanentError("dead on arrival")


@jobs.job("test_global_probe", tenant_scoped=False)
def _global_probe(db: Session, ctx: JobContext) -> None:
    _seen[str(ctx.id)] = db.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar_one()


def _row(engine: Engine, job_id: UUID | str) -> Any:
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM jobs WHERE id = :i"), {"i": str(job_id)}).mappings().one()


def _enqueue(app: FastAPI, kind: str, tenant_id: UUID | None = None, **payload: str) -> UUID:
    with transaction(app.state.engine) as db:
        job_id = jobs.enqueue(db, kind, tenant_id=tenant_id, payload=payload)
    assert job_id is not None
    return job_id


def _upload(api: Api, name: str, body: bytes, mime: str, kind: str = "import", **headers: str) -> Any:
    h = {"Origin": "https://testserver", "X-CSRF-Token": api.csrf(), **headers}
    return api.client.post(
        "/api/v1/files", files={"file": (name, body, mime)}, data={"kind": kind}, headers=h
    )


# ---- queue -----------------------------------------------------------------------------------------


def test_a_job_that_kills_its_worker_on_the_last_attempt_is_dead_lettered(
    app: FastAPI, clk: FakeClock, admin_engine: Engine
) -> None:
    job_id = _enqueue(app, "test_flaky", fail_times="0")  # max_attempts=3 (tests/security/test_jobs.py)
    with admin_engine.begin() as conn:  # the third attempt crashed the process: lease expired
        conn.execute(
            text("UPDATE jobs SET status = 'running', attempts = 3, locked_until = :t WHERE id = :i"),
            {"t": clk.now(), "i": job_id},
        )
    clk.advance(1)
    drain(app)
    row = _row(admin_engine, job_id)
    assert row["status"] == "failed" and row["attempts"] == 3
    assert "worker stopped during the final attempt" in row["last_error"]


def test_a_stale_worker_cannot_overwrite_a_reclaimed_job(app: FastAPI, admin_engine: Engine) -> None:
    job_id = _enqueue(app, "test_flaky", fail_times="0")
    got = jobs._claim(app.state.engine)
    assert got is not None
    stale, _ = got
    assert stale.id == job_id
    with admin_engine.begin() as conn:  # lease expired, another worker took attempt 2
        conn.execute(text("UPDATE jobs SET attempts = 2 WHERE id = :i"), {"i": job_id})
    with pytest.raises(jobs.LeaseLost), transaction(app.state.engine) as db:
        jobs._finish(db, stale, "succeeded")
    row = _row(admin_engine, job_id)
    assert row["status"] == "running" and row["attempts"] == 2


def test_a_failing_dead_letter_hook_never_blocks_the_dead_letter(app: FastAPI, admin_engine: Engine) -> None:
    job_id = _enqueue(app, "test_hook_fails")
    drain(app)
    assert _row(admin_engine, job_id)["status"] == "failed"


def test_global_jobs_never_inherit_a_tenant_from_the_pooled_connection(
    migrated_db: DbUrls, app: FastAPI, api: Api
) -> None:
    full_admin(api, unique_email("pool"))
    tenant = UUID(api.get("/auth/me").json()["tenant"]["id"])
    one_conn = create_engine(migrated_db.app, pool_size=1, max_overflow=0)
    try:
        tenant_job = _enqueue(app, "test_tenant_probe", tenant)
        while jobs.run_once(one_conn, app.state.settings):  # tenant job first, same connection after
            pass
        global_job = _enqueue(app, "test_global_probe")
        while jobs.run_once(one_conn, app.state.settings):
            pass
    finally:
        one_conn.dispose()
    assert tenant_job and _seen[str(global_job)] in (None, "")


def test_dead_password_reset_does_not_keep_the_typed_address(
    migrated_db: DbUrls, clk: FakeClock, admin_engine: Engine, app: FastAPI, api: Api
) -> None:
    account = full_admin(api, unique_email("deadreset"))
    no_smtp = make_settings(migrated_db.app, smtp_port=9)  # nothing listens: every delivery fails
    request_id = new_id()
    with admin_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO password_reset_requests (id, email) VALUES (:i, :e)"),
            {"i": request_id, "e": account.email},
        )
    job_id = _enqueue(app, "identity_password_reset", request_id=str(request_id))
    for _ in range(6):
        while jobs.run_once(app.state.engine, no_smtp):
            pass
        clk.advance(4000)
    assert _row(admin_engine, job_id)["status"] == "failed"
    with admin_engine.connect() as conn:
        left = conn.execute(
            text("SELECT count(*) FROM password_reset_requests WHERE id = :i"), {"i": request_id}
        ).scalar_one()
    assert left == 0


def test_worker_registers_every_job_kind_that_code_enqueues() -> None:
    import rebate.workers.__main__  # noqa: F401  (the worker's own imports)

    expected = {
        "identity_registration_email",
        "identity_password_reset",
        "identity_email_change",
        "tenancy_invitation_email",
        "files_scan",
        "files_purge_quarantine",
        "files_notify_blocked",
    }
    assert expected <= set(jobs.REGISTRY)


# ---- files -----------------------------------------------------------------------------------------


@pytest.fixture
def admin(api: Api) -> Api:
    storage.ensure_bucket(api.client.app.state.settings)  # type: ignore[attr-defined]
    full_admin(api, unique_email("s2rev"))
    return api


def test_scan_failure_notifies_admins_and_a_reupload_supersedes_the_failed_row(
    migrated_db: DbUrls, clk: FakeClock, admin_engine: Engine
) -> None:
    broken = make_app(migrated_db.app, clamd_host="127.0.0.1", clamd_port=9)
    storage.ensure_bucket(make_settings(migrated_db.app))
    body = samples.CSV + new_id().hex.encode() + b",1\n"
    with api_client(broken) as api:
        account = full_admin(api, unique_email("scanerr"))
        failed_id = _upload(api, "first.csv", body, "text/csv").json()["id"]
        for _ in range(6):
            drain(broken)
            clk.advance(4000)
        login(api, account)
        assert api.get(f"/files/{failed_id}").json()["scan_status"] == "error"
        subjects = [m["Subject"] for m in messages_to(account.email, expect=2)]
        assert "Veyra Recover could not scan an upload" in subjects
        job_id = next(
            j["id"]
            for j in api.get("/jobs", params={"status": "failed"}).json()["data"]
            if j["kind"] == "files_scan"
        )
    fixed = make_app(migrated_db.app)
    with api_client(fixed) as api:
        login(api, account)
        again = _upload(api, "again.csv", body, "text/csv")
        assert again.status_code == 201  # the error row does not own the content
        drain(fixed)
        assert api.post(f"/jobs/{job_id}/retry").status_code == 200
        drain(fixed)
        old = api.get(f"/files/{failed_id}").json()
    assert (
        old["scan_status"] == "error"
        and old["scan_detail"] == f"superseded by re-upload {again.json()['id']}"
    )
    assert _row(admin_engine, job_id)["status"] == "succeeded"  # no orphan copy, no integrity error


def test_promotion_purges_quarantine_after_commit_and_rescan_is_a_no_op(
    app: FastAPI, admin: Api, admin_engine: Engine
) -> None:
    file_id = _upload(admin, "p.csv", samples.CSV + new_id().hex.encode() + b",2\n", "text/csv").json()["id"]
    tenant = UUID(admin.get("/auth/me").json()["tenant"]["id"])
    drain(app)
    with pytest.raises(ClientError):
        storage.client(app.state.settings).head_object(
            Bucket=app.state.settings.s3_bucket, Key=storage.quarantine_key(tenant, UUID(file_id))
        )
    rerun = _enqueue(app, "files_scan", tenant, file_id=file_id)
    drain(app)
    assert _row(admin_engine, rerun)["status"] == "succeeded"
    assert admin.get(f"/files/{file_id}").json()["scan_status"] == "clean"


def _nested_zip(depth: int) -> bytes:
    body = b"SYNTHETIC"
    for i in range(depth):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"layer{i}.zip" if i else "inner.txt", body)
        body = buf.getvalue()
    return body


def test_content_clamd_cannot_fully_scan_is_never_clean(migrated_db: DbUrls) -> None:
    result = scanner.scan(make_settings(migrated_db.app), [_nested_zip(30)])  # > MaxRecursion (17)
    assert not result.clean and "Exceeded" in (result.signature or "")


def test_a_clamd_error_reply_is_never_clean(migrated_db: DbUrls) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def reply() -> None:
        conn, _ = server.accept()
        conn.recv(65536)
        conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
        conn.close()

    threading.Thread(target=reply, daemon=True).start()
    settings = make_settings(migrated_db.app, clamd_host="127.0.0.1", clamd_port=server.getsockname()[1])
    try:
        with pytest.raises(scanner.ScannerError):
            scanner.scan(settings, [b"data"])
    finally:
        server.close()


def _book(
    parts: dict[str, bytes], types: bytes | None = None, encrypt: bool = False, workbook: bool = True
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", types or b"<Types/>")
        if workbook:
            zf.writestr("xl/workbook.xml", b"<workbook/>")
        for name, data in parts.items():
            zf.writestr(name, data)
            if encrypt:  # writestr resets the flags; the central directory is written on close
                zf.getinfo(name).flag_bits |= 0x1
    return buf.getvalue()


def _duplicate_entries() -> bytes:
    buf = io.BytesIO()
    with pytest.warns(UserWarning), zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("xl/workbook.xml", b"<workbook/>")
        zf.writestr("xl/workbook.xml", b"<workbook/>")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (_book({"xl/vbaProject.bin": b"\xd0\xcf\x11\xe0"}), "macros"),  # VBA part, harmless content types
        (
            _book(
                {},
                types=b"<Types><Default ContentType='application/vnd.ms-excel.sheet.macroEnabled.main+xml'/></Types>",
            ),
            "macros",
        ),
        (_book({"xl/macrosheets/sheet1.xml": b"<xm/>"}), "macros"),  # Excel 4 macro sheet
        (_book({"xl/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0"}), "macros"),
        (_book({"xl/externalLinks/externalLink1.xml": b"<x/>"}), "macros"),
        (_book({"xl/sheet.xml": b"<s/>"}, encrypt=True), "encrypted"),
        (_book({"xl/a.xml": b"<a/>"} | {f"xl/p{i}.xml": b"" for i in range(10_001)}), "archive_too_complex"),
        (_book({"xl/styles.xml": b"<s/>", "_rels/.rels": b"<r/>"}, workbook=False), "not_a_workbook"),
    ],
    ids=[
        "vba",
        "macro-content-type",
        "macrosheet",
        "ole-embedding",
        "external-link",
        "encrypted",
        "too-many-entries",
        "no-workbook",
    ],
)
def test_xlsx_active_content_and_structure_are_rejected(admin: Api, body: bytes, code: str) -> None:
    r = _upload(admin, "book.xlsx", body, XLSX_MIME)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"][0]["code"] == code


def test_oversized_content_types_part_is_rejected_without_inflating_it() -> None:
    # libmagic already refuses such a file at upload; this pins the bounded read in _check_xlsx itself
    body = _book({}, types=b"<Types>" + os.urandom(35_000).hex().encode() + b"</Types>")
    with pytest.raises(UploadRejected) as err:
        _check_xlsx(io.BytesIO(body))
    assert err.value.details[0]["code"] == "not_a_workbook"


def test_duplicate_zip_entries_are_rejected(admin: Api) -> None:
    r = _upload(admin, "dup.xlsx", _duplicate_entries(), XLSX_MIME)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "archive_too_complex"


def test_non_latin_filenames_keep_their_extension(admin: Api) -> None:
    assert sanitise_filename("отчёт.csv") == "_____.csv"
    assert sanitise_filename("Café.csv") == "Cafe.csv"
    r = _upload(admin, "отчёт.csv", samples.CSV + new_id().hex.encode() + b",4\n", "text/csv")
    assert r.status_code == 201 and r.json()["original_name"].endswith(".csv")


# ---- upload gate (before the body is read) ---------------------------------------------------------


def test_upload_needs_a_session_and_csrf_before_the_body_is_read(app: FastAPI, api: Api) -> None:
    anonymous = _upload(api, "a.csv", samples.CSV, "text/csv")
    assert anonymous.status_code == 401
    full_admin(api, unique_email("gate"))
    forged = _upload(api, "a.csv", samples.CSV, "text/csv", **{"X-CSRF-Token": "forged"})
    assert forged.status_code == 403 and forged.json()["error"]["code"] == "csrf_failed"
    cross_site = _upload(api, "a.csv", samples.CSV, "text/csv", Origin="https://evil.example")
    assert cross_site.status_code == 403


def test_chunked_and_unsized_uploads_are_refused(admin: Api) -> None:
    h = {"Origin": "https://testserver", "X-CSRF-Token": admin.csrf()}
    chunked = admin.client.post(
        "/api/v1/files",
        content=iter([b"--x\r\n"]),
        headers={**h, "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert chunked.status_code == 411 and chunked.json()["error"]["code"] == "length_required"


def test_uploads_are_rate_limited_per_user(admin: Api) -> None:
    statuses = [_upload(admin, "e.csv", b"", "text/csv").status_code for _ in range(31)]
    assert statuses[:30] == [422] * 30 and statuses[30] == 429
