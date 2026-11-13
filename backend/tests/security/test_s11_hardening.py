"""S11.3 review fixes: log scrubbing, security headers (API + Caddyfile), upload corpus gaps,
session revocation on password change."""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from rebate.modules.files.validation import UploadRejected, _check_xlsx
from tests.security import samples
from tests.security.flows import full_admin, login, step_up
from tests.security.test_files import upload
from tests.security.test_s2_review import XLSX_MIME
from tests.support import STRONG_PASSWORD, Api, FakeClock, api_client, unique_email

# ---- 1. SQL parameters never reach the logs ---------------------------------------------------------


def test_unhandled_db_error_logs_type_and_request_id_not_parameters(
    app: FastAPI, clk: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = unique_email("sqlparam")

    @app.get("/api/v1/_boom")
    def boom() -> None:
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT CAST(:e AS integer)"), {"e": secret})

    with api_client(app) as api:
        r = api.get("/_boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    out = capsys.readouterr().out
    assert secret not in out and "parameters" not in out
    failed = [json.loads(x) for x in out.splitlines() if x.startswith("{") and "unhandled_error" in x]
    assert failed and failed[0]["error"] == "DataError" and failed[0]["request_id"]


# ---- 3. security headers ----------------------------------------------------------------------------

API_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "cache-control": "no-store",
}


def test_api_security_headers_on_200_4xx_and_500(app: FastAPI, clk: FakeClock) -> None:
    @app.get("/api/v1/_boom")
    def boom() -> None:
        raise RuntimeError("boom")

    with api_client(app) as api:
        responses = {
            204: api.get("/auth/csrf"),
            401: api.get("/auth/me"),
            404: api.get("/nope"),
            500: api.get("/_boom"),
        }
    for status, r in responses.items():
        assert r.status_code == status
        for name, value in API_HEADERS.items():
            assert r.headers.get(name) == value, (status, name)


CADDYFILE = Path(__file__).resolve().parents[3] / "infra" / "caddy" / "Caddyfile"


def _site_headers(site: str) -> str:
    blocks = re.split(r"^(?=\{\$)", CADDYFILE.read_text(), flags=re.M)
    (block,) = [b for b in blocks if b.startswith(site)]
    return re.search(r"header \{(.*?)\n\t\}", block, re.S).group(1)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        (
            "{$APP_DOMAIN}",
            [
                'Strict-Transport-Security "max-age=31536000',
                "X-Content-Type-Options nosniff",
                "X-Frame-Options DENY",
                "Referrer-Policy strict-origin-when-cross-origin",
                "Content-Security-Policy \"default-src 'self'; frame-ancestors 'none'",
            ],
        ),
        (
            "{$S3_DOMAIN}",
            [
                'Strict-Transport-Security "max-age=31536000',
                "X-Content-Type-Options nosniff",
                "X-Frame-Options DENY",
                "Content-Security-Policy \"default-src 'none'; sandbox\"",
            ],
        ),
    ],
)
def test_caddyfile_sets_security_headers(site: str, expected: list[str]) -> None:
    headers = _site_headers(site)
    for line in expected:
        assert line in headers, line


# ---- 2/7. upload corpus gaps ------------------------------------------------------------------------


def test_archive_too_large_is_rejected_below_the_ratio_limit() -> None:
    buf = io.BytesIO()  # stored (ratio 1): over the size cap while far under the 100:1 ratio cap
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("xl/sharedStrings.xml", b"\x00" * (101 * 1024 * 1024))
    with pytest.raises(UploadRejected) as e:
        _check_xlsx(buf)
    assert e.value.details[0]["code"] == "archive_too_large" and "CSV" in e.value.details[0]["message"]


@pytest.fixture
def admin(api: Api) -> Api:
    full_admin(api, unique_email("s11up"))
    return api


def _book_with(parts: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in {
            "[Content_Types].xml": samples._CONTENT_TYPES,
            "xl/workbook.xml": b"<workbook/>",
            **parts,
        }.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_xml_entity_bomb_in_a_workbook_is_not_expanded(admin: Api) -> None:
    lol = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><sst>&b;&b;</sst>'
    r = upload(admin, "lol.xlsx", _book_with({"xl/sharedStrings.xml": lol}), XLSX_MIME)
    # either the validator refuses it, or a later parse must (defusedxml): never accepted silently as parsed
    assert r.status_code == 422, r.text


def test_polyglot_pdf_zip_is_rejected(admin: Api) -> None:
    r = upload(admin, "poly.pdf", samples.PDF + samples.xlsx(), "application/pdf", kind="agreement")
    assert r.status_code == 422, r.text


# ---- 5. password change revokes the user's other sessions -------------------------------------------


def test_password_change_revokes_other_sessions(api: Api, api2: Api, clk: FakeClock) -> None:
    account = full_admin(api, unique_email("pwrevoke"))
    clk.advance(60)
    login(api2, account)
    assert api2.get("/auth/me").status_code == 200
    clk.advance(60)
    step_up(api, account)
    r = api.post(
        "/auth/password/change",
        json={"current_password": STRONG_PASSWORD, "new_password": "another strong passphrase 99"},
    )
    assert r.status_code == 200, r.text
    assert api.get("/auth/me").status_code == 200  # the session that changed it survives
    assert api2.get("/auth/me").status_code == 401  # every other one is gone


def test_worker_has_resource_limits_in_prod_compose() -> None:
    compose = (CADDYFILE.parents[1] / "docker" / "compose.prod.yml").read_text()
    worker = compose.split("\n  worker:\n")[1].split("\n  caddy:\n")[0]
    assert 'tmpfs: ["/tmp:size=512m"]' in worker  # counts toward mem_limit (3g), so it stays small
    for key in ("mem_limit:", "cpus:", "pids_limit:"):
        assert key in worker, key


def test_db_errors_hide_bound_parameters(app: FastAPI) -> None:
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError) as e, app.state.engine.connect() as conn:
        conn.execute(text("SELECT CAST(:e AS integer)"), {"e": "param-value-x"})
    assert "[SQL parameters hidden" in str(e.value)
