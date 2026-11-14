"""Uploads, malware scanning, quarantine -> clean promotion, presigned downloads, against the real
SeaweedFS and ClamAV (FR-FI-01..04, SECURITY.md §4, ADR-017 conditions, S2 acceptance)."""

import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.security import samples
from tests.security.flows import full_admin
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


@pytest.fixture(scope="session", autouse=True)
def bucket(migrated_db: DbUrls) -> None:
    storage.ensure_bucket(make_settings(migrated_db.app))


def upload(api: Api, name: str, body: bytes, mime: str, kind: str = "import") -> Any:
    csrf = api.csrf()
    return api.client.post(
        "/api/v1/files",
        files={"file": (name, body, mime)},
        data={"kind": kind},
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
    )


def head(app: FastAPI, key: str) -> dict[str, Any]:
    s = app.state.settings
    return dict(storage.client(s).head_object(Bucket=s.s3_bucket, Key=key))


@pytest.fixture
def admin(api: Api) -> Iterator[Api]:
    full_admin(api, unique_email("files"))
    yield api


def test_clean_csv_is_promoted_out_of_quarantine(app: FastAPI, admin: Api) -> None:
    r = upload(admin, "sales Q3 (SYNTHETIC).csv", samples.CSV, "text/csv")
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["scan_status"] == "pending" and f["original_name"] == "sales Q3 (SYNTHETIC).csv"
    pending = admin.get(f"/files/{f['id']}/download-url")
    assert pending.status_code == 409  # nothing is readable before the scan says clean
    drain(app)
    done = admin.get(f"/files/{f['id']}").json()
    assert done["scan_status"] == "clean" and done["scanned_at"]


def test_quarantine_copy_versioning_and_encryption(app: FastAPI, admin: Api, admin_engine: Engine) -> None:
    r = upload(admin, "promote.csv", samples.CSV + b"x,1\n", "text/csv")
    file_id = r.json()["id"]
    with admin_engine.connect() as conn:
        tenant, q_key = conn.execute(
            text("SELECT tenant_id, storage_key FROM file_objects WHERE id = :i"), {"i": file_id}
        ).one()
    assert q_key == f"quarantine/{tenant}/{file_id}"
    assert head(app, q_key)["ServerSideEncryption"] == "AES256"
    drain(app)
    with admin_engine.connect() as conn:
        key = conn.execute(
            text("SELECT storage_key FROM file_objects WHERE id = :i"), {"i": file_id}
        ).scalar_one()
    assert key.startswith(f"tenants/{tenant}/imports/") and key.endswith(f"/{file_id}")
    assert head(app, key)["ServerSideEncryption"] == "AES256"  # the copy is encrypted too
    s = app.state.settings
    s3 = storage.client(s)
    assert s3.get_bucket_versioning(Bucket=s.s3_bucket)["Status"] == "Enabled"
    rules = s3.get_bucket_encryption(Bucket=s.s3_bucket)["ServerSideEncryptionConfiguration"]["Rules"]
    assert rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
    with pytest.raises(ClientError):  # quarantine copy removed after promotion ...
        head(app, q_key)
    versions = s3.list_object_versions(Bucket=s.s3_bucket, Prefix=q_key)
    assert versions.get("Versions"), "... but versioning keeps the history"
    body = s3.get_object(Bucket=s.s3_bucket, Key=key)["Body"].read()
    assert body == samples.CSV + b"x,1\n"


def test_presigned_url_downloads_then_expires(migrated_db: DbUrls, clk: FakeClock) -> None:
    app = make_app(migrated_db.app, presigned_url_seconds=10)
    with api_client(app) as api:
        full_admin(api, unique_email("presign"))
        file_id = upload(api, "download.csv", samples.CSV + b"y,2\n", "text/csv").json()["id"]
        drain(app)
        r = api.get(f"/files/{file_id}/download-url")
        assert r.status_code == 200 and r.json()["expires_in_seconds"] == 10
        url = r.json()["url"]
        with urllib.request.urlopen(url) as resp:  # noqa: S310 (our own test storage)
            assert resp.read() == samples.CSV + b"y,2\n"
            assert resp.headers["Content-Disposition"] == 'attachment; filename="download.csv"'
            assert resp.headers["Content-Type"].startswith("text/csv")
        time.sleep(11)  # presigned expiry is enforced by the storage server's real clock
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(url)  # noqa: S310
        assert err.value.code == 403
        actions = [e["action"] for e in api.get("/audit-log", params={"entity_id": file_id}).json()["data"]]
    assert {"file.uploaded", "file.scanned_clean", "file.download_url_issued"} <= set(actions)


def test_eicar_is_infected_blocked_and_admins_are_told(app: FastAPI, admin: Api) -> None:
    me = admin.get("/auth/me").json()["user"]["email"]
    r = upload(admin, "remit.csv", samples.eicar(), "text/csv", kind="attachment")
    assert r.status_code == 201, r.text
    file_id = r.json()["id"]
    drain(app)
    f = admin.get(f"/files/{file_id}").json()
    assert f["scan_status"] == "infected" and "Eicar" in (f["scan_detail"] or "")
    blocked = admin.get(f"/files/{file_id}/download-url")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "invalid_state"
    notice = messages_to(me, expect=2)[0]
    assert notice["Subject"] == "Veyra Recover blocked an infected upload"


@pytest.mark.parametrize(
    ("name", "body", "mime", "code"),
    [
        (
            "book.xlsm",
            samples.xlsx(macros=True),
            "application/vnd.ms-excel.sheet.macroEnabled.12",
            "type_not_allowed",
        ),
        ("renamed.xlsx", samples.xlsx(macros=True), XLSX_MIME, "macros"),
        ("old.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 600, "application/vnd.ms-excel", "type_not_allowed"),
        ("setup.exe", samples.EXE, "application/octet-stream", "type_not_allowed"),
        ("invoice.csv", samples.EXE, "text/csv", "content_type_mismatch"),  # renamed executable
        ("invoice.xlsx", samples.CSV, XLSX_MIME, "content_type_mismatch"),  # CSV pretending to be XLSX
        ("data.csv", samples.CSV, "application/pdf", "declared_type_mismatch"),
        ("bomb.xlsx", samples.xlsx(bomb_mb=60), XLSX_MIME, "archive_ratio"),
        ("page.html", b"<html><script>alert(1)</script></html>", "text/html", "type_not_allowed"),
        ("noext", samples.CSV, "text/csv", "type_not_allowed"),
        ("empty.csv", b"", "text/csv", "empty"),
    ],
)
def test_disallowed_and_mismatched_files_are_rejected(
    admin: Api, name: str, body: bytes, mime: str, code: str
) -> None:
    r = upload(admin, name, body, mime)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"][0]["code"] == code


def test_pdf_and_xlsx_are_accepted(app: FastAPI, admin: Api) -> None:
    assert upload(admin, "contract.pdf", samples.PDF, "application/pdf", kind="agreement").status_code == 201
    assert upload(admin, "sales.xlsx", samples.xlsx(marker=b"a1"), XLSX_MIME).status_code == 201
    drain(app)
    statuses = {f["original_name"]: f["scan_status"] for f in admin.get("/files").json()["data"]}
    assert statuses["contract.pdf"] == "clean" and statuses["sales.xlsx"] == "clean"


def test_oversized_files_are_rejected(migrated_db: DbUrls, clk: FakeClock) -> None:
    app = make_app(migrated_db.app, upload_max_mb_spreadsheet=1, upload_max_mb_pdf=1)
    with api_client(app) as api:
        full_admin(api, unique_email("big"))
        just_over = b"a,b\n" + b"1,2\n" * (1024 * 1024 // 4)  # > 1 MiB, < limit + multipart overhead
        r = upload(api, "big.csv", just_over, "text/csv")
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
        way_over = b"a,b\n" + b"1,2\n" * (3 * 1024 * 1024 // 4)
        early = upload(api, "huge.csv", way_over, "text/csv")  # refused by Content-Length before parsing
        assert early.status_code == 413


def test_duplicate_uploads_are_detected_per_tenant_and_kind(app: FastAPI, admin: Api, api2: Api) -> None:
    body = samples.CSV + b"dup,3\n"
    first = upload(admin, "a.csv", body, "text/csv")
    second = upload(admin, "renamed.csv", body, "text/csv")
    assert first.status_code == 201
    assert second.status_code == 409 and second.json()["error"]["code"] == "duplicate_file"
    assert second.json()["error"]["details"][0]["message"] == first.json()["id"]
    assert upload(admin, "a.csv", body, "text/csv", kind="attachment").status_code == 201  # not a dedup kind
    full_admin(api2, unique_email("othertenant"))
    assert upload(api2, "a.csv", body, "text/csv").status_code == 201  # another workspace may have it


def test_filenames_are_sanitised(admin: Api) -> None:
    r = upload(admin, '..\\..\\evil"\r\nname<script>.csv', samples.CSV + b"n,9\n", "text/csv")
    assert r.status_code == 201
    name = r.json()["original_name"]
    assert name.endswith(".csv") and not any(c in name for c in '"\\/<>\r\n')


def test_export_files_need_the_permission_of_the_export_that_made_them(
    app: FastAPI, admin: Api, admin_engine: Engine
) -> None:
    """Export files: batch export -> claim_batch.export (A P N), audit pack -> audit_pack.export (A P V),
    missed-claims list -> data.read (all). Viewer/analyst get 403 on download-url and metadata, and the list
    leaves the file out. Other kinds stay readable by every data.read role."""
    from contextlib import ExitStack

    from tests.integration.agreements_support import member

    up = upload(admin, "plain.csv", samples.CSV + b"exp,1\n", "text/csv").json()
    drain(app)
    ids: dict[str, str] = {}
    with admin_engine.begin() as conn:
        for label, name in (
            ("batch", "CB-2026-000001.csv"),
            ("pack", "audit-pack-CB-2026-000001.csv"),
            ("missed", "missed-claims-2026-07-10.csv"),
        ):
            fid = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO file_objects (tenant_id, id, kind, original_name, storage_key, size_bytes, "
                    "sha256, detected_mime, scan_status, uploaded_by, scanned_at) "
                    "SELECT tenant_id, CAST(:i AS uuid), 'export', :n, 'tenants/' || tenant_id || '/exports/x/' || CAST(:i AS text), 10, :h, "
                    "'text/csv', 'clean', uploaded_by, now() FROM file_objects WHERE id = :f"
                ),
                {"i": fid, "n": name, "h": label.encode().hex().ljust(64, "0"), "f": up["id"]},
            )
            ids[label] = fid
    with ExitStack() as stack:
        viewer = member(stack, app, admin, "viewer")
        analyst = member(stack, app, admin, "analyst")
        allowed = {  # who may read which (download-url would be 409/200, never 403)
            "batch": {"admin": True, "viewer": False, "analyst": True},
            "pack": {"admin": True, "viewer": True, "analyst": False},
            "missed": {"admin": True, "viewer": True, "analyst": True},
        }
        for who, api in (("admin", admin), ("viewer", viewer), ("analyst", analyst)):
            listed = {f["id"] for f in api.get("/files", params={"limit": 200}).json()["data"]}
            assert up["id"] in listed
            for label, fid in ids.items():
                ok = allowed[label][who]
                assert (fid in listed) is ok, (who, label)
                url = api.get(f"/files/{fid}/download-url")
                meta = api.get(f"/files/{fid}")
                if ok:
                    assert url.status_code != 403 and meta.status_code == 200, (who, label, url.text)
                else:
                    assert url.status_code == 403 and meta.status_code == 403, (who, label, url.text)
