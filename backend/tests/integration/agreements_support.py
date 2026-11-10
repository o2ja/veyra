"""Helpers for the S4 agreement tests: SYNTHETIC manual payloads, members with roles, the draft pipeline."""

import copy
import json
import uuid
from contextlib import ExitStack
from typing import Any

from fastapi import FastAPI

from tests.security.flows import Account, enable_mfa, login
from tests.support import STRONG_PASSWORD, Api, api_client, latest_token, unique_email
from tests.testdata import data

PAYLOAD_NAMES = (
    "SPA-VOLT-2026-884",
    "SPA-VOLT-2026-901",
    "SPA-BRIT-2026-031",
    "SPA-COPR-2026-107",
    "REB-VOLT-2026",
    "REB-NWND-2026",
)


def payload(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(data(f"agreements/manual_payloads/{name}.json"))
    return copy.deepcopy(loaded)


def member(stack: ExitStack, app: FastAPI, admin: Api, role: str) -> Api:
    """Invite + accept + sign in + MFA, in a browser of its own (closed with ``stack``)."""
    email = unique_email(role)
    r = admin.post("/invitations", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    token = latest_token(email)
    api = stack.enter_context(api_client(app))
    r = api.post(
        "/invitations/accept", json={"token": token, "full_name": role.title(), "password": STRONG_PASSWORD}
    )
    assert r.status_code == 200, r.text
    account = Account(email=email)
    login(api, account)
    enable_mfa(api, account)
    return api


def create(api: Api, body: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    r = api.post("/agreement-drafts", json={"source": "manual", **(body or {}), **fields})
    assert r.status_code == 201, r.text
    return dict(r.json())


def update(api: Api, draft: dict[str, Any], new_payload: dict[str, Any]) -> Any:
    return api.put(
        f"/agreement-drafts/{draft['id']}",
        json={"payload": new_payload},
        headers={"If-Match": f'"{draft["revision"]}"'},
    )


def submit(api: Api, draft_id: str, acks: list[str] | None = None) -> Any:
    return api.post(f"/agreement-drafts/{draft_id}/submit", json={"acknowledged_warnings": acks or []})


def approve(api: Api, draft_id: str, key: str | None = None) -> Any:
    return api.post(
        f"/agreement-drafts/{draft_id}/approve", headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )


def activate(maker: Api, approver: Api, body: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Draft -> submit -> approve; returns the resulting version detail."""
    d = create(maker, payload=body, **fields)
    assert d["validation"]["valid"], d["validation"]
    r = submit(maker, d["id"], [w["id"] for w in d["validation"]["warnings"]])
    assert r.status_code == 200, r.text
    r = approve(approver, d["id"])
    assert r.status_code == 200, r.text
    v = approver.get(f"/agreement-versions/{r.json()['resulting_version_id']}")
    assert v.status_code == 200, v.text
    return dict(v.json())


def codes(validation: dict[str, Any], kind: str = "errors") -> set[tuple[str, str]]:
    return {(e["field"], e["code"]) for e in validation[kind]}
