"""Helpers for the end-to-end extraction tests (real API, worker loop in-process, recorded provider)."""

from typing import Any

from tests.integration.conftest import Workspace
from tests.support import drain
from tests.testdata import data

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def set_ai(ws: Workspace, enabled: bool) -> None:
    etag = ws.api.get("/settings").headers["ETag"]
    r = ws.api.patch("/settings", json={"ai_processing_enabled": enabled}, headers={"If-Match": etag})
    assert r.status_code == 200, r.text


def agreement_file(ws: Workspace, name: str, body: bytes, mime: str = PDF) -> str:
    r = ws.api.client.post(
        "/api/v1/files",
        files={"file": (name, body, mime)},
        data={"kind": "agreement"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": ws.api.csrf()},
    )
    assert r.status_code == 201, r.text
    drain(ws.app)
    f = ws.api.get(f"/files/{r.json()['id']}").json()
    assert f["scan_status"] == "clean", f
    return str(f["id"])


def golden_file(ws: Workspace, stem: str) -> str:
    ext = "xlsx" if stem.endswith("_items") else "pdf"
    return agreement_file(
        ws, f"{stem}.{ext}", data(f"agreements/{stem}.{ext}"), XLSX if ext == "xlsx" else PDF
    )


def start(ws: Workspace, file_id: str, **extra: Any) -> Any:
    return ws.api.post("/document-extractions", json={"file_id": file_id, **extra})


def extract(ws: Workspace, file_id: str, **extra: Any) -> dict[str, Any]:
    """Request, run the job in-process, return the extraction detail."""
    r = start(ws, file_id, **extra)
    assert r.status_code in (200, 202), r.text
    drain(ws.app)
    d = ws.api.get(f"/document-extractions/{r.json()['id']}")
    assert d.status_code == 200, d.text
    return dict(d.json())


def draft_of(ws: Workspace, extraction: dict[str, Any]) -> dict[str, Any]:
    assert extraction["draft_id"], extraction
    r = ws.api.get(f"/agreement-drafts/{extraction['draft_id']}")
    assert r.status_code == 200, r.text
    return dict(r.json())
