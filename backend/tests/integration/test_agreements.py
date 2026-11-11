"""S4.9 (backend): the agreement draft pipeline through the real API, Postgres and job queue.

Expected values come from the SYNTHETIC payload files (testdata/generated/agreements/manual_payloads),
BUSINESS_LOGIC.md §3.3/§9.2 and hand derivation (split dates), never from the code under test.
"""

import base64
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from rebate.modules.files import storage
from tests.conftest import DbUrls
from tests.integration.agreements_support import (
    PAYLOAD_NAMES,
    activate,
    approve,
    codes,
    create,
    member,
    payload,
    submit,
    update,
)
from tests.integration.conftest import XLSX, Workspace, clean_file, new_workspace
from tests.support import Api, api_client, drain, fake_clock, make_app
from tests.testdata import data

NOW = datetime(
    2026, 6, 15, 15, 0, tzinfo=UTC
)  # business date 2026-06-15 in America/Chicago: all payloads in force


@dataclass
class Org:
    ws: Workspace
    maker: Api  # admin: creates and edits drafts
    p1: Api  # approvers
    p2: Api


@pytest.fixture(scope="module")
def org(migrated_db: DbUrls) -> Iterator[Org]:
    app = make_app(migrated_db.app)
    storage.ensure_bucket(app.state.settings)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        maker = stack.enter_context(api_client(app))
        ws = new_workspace(app, maker)
        yield Org(ws, maker, member(stack, app, maker, "approver"), member(stack, app, maker, "approver"))


def fresh(name: str) -> dict[str, Any]:
    """A payload file with a unique agreement number (tests share one workspace)."""
    p = payload(name)
    p["header"]["external_number"] += f"-{uuid.uuid4().hex[:6].upper()}"
    return p


def items_of(api: Api, version_id: str) -> list[dict[str, Any]]:
    r = api.get(f"/agreement-versions/{version_id}/items", params={"limit": 200})
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


def _money(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


# ---- acceptance: every manual payload, by hand -------------------------------------------------------------


@pytest.mark.parametrize("name", PAYLOAD_NAMES)
def test_manual_payload_is_accepted_and_activated(org: Org, name: str, admin_engine: Engine) -> None:
    body = payload(name)
    v = activate(org.maker, org.p1, body)
    h = body["header"]
    assert (v["status"], v["version_number"], v["source"]) == ("active", 1, "manual")
    assert (v["valid_from"], v["valid_to"]) == (h["valid_from"], h["valid_to"])
    assert (v["external_number"], v["name"], v["agreement_type"]) == (
        h["external_number"],
        h["name"],
        h["agreement_type"],
    )
    assert v["manufacturer_id"] == org.ws.manufacturers[h["manufacturer_code"]]
    assert v["rules"] == body["rules"]
    assert v["item_count"] == len(body["items"])
    assert {(c["customer_code"], c["scope"]) for c in v["customers"]} == {
        (c["customer_code"], c["scope"]) for c in body["customers"]
    }
    assert [(t["tier_number"], Decimal(t["lower_bound"]), Decimal(t["rate"])) for t in v["tiers"]] == [
        (t["tier_number"], Decimal(t["lower_bound"]), Decimal(t["rate"])) for t in body["tiers"]
    ]
    stored = {i["sku"]: i for i in items_of(org.p1, v["id"])}
    paged, cursor = [], None  # keyset pages in line order, cursor opaque
    while True:
        r = org.p1.get(
            f"/agreement-versions/{v['id']}/items",
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        paged += [i["sku"] for i in r.json()["data"]]
        if not r.json()["page"]["has_more"]:
            break
        cursor = r.json()["page"]["next_cursor"]
    assert paged == [i["sku"] for i in body["items"]]
    past_end = (
        base64.urlsafe_b64encode(b"999999").decode().rstrip("=")
    )  # an empty page (also: a rebate version)
    r = org.p1.get(f"/agreement-versions/{v['id']}/items", params={"cursor": past_end})
    assert r.status_code == 200 and r.json()["data"] == [] and r.json()["page"]["has_more"] is False
    assert set(stored) == {i["sku"] for i in body["items"]}
    for item in body["items"]:
        for f in ("reference_cost", "deviated_cost", "sell_price", "rate", "flat_amount", "max_quantity"):
            assert _money(stored[item["sku"]][f]) == _money(item.get(f)), (item["sku"], f)
        assert stored[item["sku"]]["claim_uom"] == item.get("claim_uom")
    listed = org.p2.get("/agreements", params={"limit": 200}).json()["data"]
    mine = next(a for a in listed if a["external_number"] == h["external_number"])
    assert mine["current_version"]["id"] == v["id"]
    with admin_engine.connect() as conn:
        actions = set(
            conn.execute(
                text("SELECT action FROM audit_log WHERE tenant_id = :t AND entity_id IN (:v, :d)"),
                {"t": org.ws.tenant_id, "v": v["id"], "d": v["draft_id"]},
            ).scalars()
        )
    assert {"agreement_draft.created", "agreement_draft.submitted", "agreement_draft.approved",
            "agreement_version.activated"} <= actions  # fmt: skip


def test_approve_is_idempotent_and_needs_a_key(org: Org) -> None:
    d = create(org.maker, payload=fresh("SPA-VOLT-2026-901"))
    assert submit(org.maker, d["id"]).status_code == 200
    assert org.p1.post(f"/agreement-drafts/{d['id']}/approve", headers={"If-Match": '"1"'}).status_code == 400
    no_match = org.p1.post(
        f"/agreement-drafts/{d['id']}/approve", headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert no_match.status_code == 412  # the approver must name the revision they reviewed
    assert approve(org.p1, d["id"], revision='"7"').status_code == 412  # stale / other revision
    key = str(uuid.uuid4())
    first = approve(org.p1, d["id"], key)
    again = approve(org.p1, d["id"], key)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    other = approve(org.p1, d["id"])
    assert other.status_code == 409 and other.json()["error"]["code"] == "invalid_state"


def test_ai_extraction_drafts_are_refused_until_s6(org: Org) -> None:
    r = org.maker.post("/agreement-drafts", json={"source": "ai_extraction"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_enabled"


# ---- validator: every rule, positive and negative --------------------------------------------------------


def _set(p: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    target: Any = p
    for k in keys[:-1]:
        target = target[int(k)] if k.isdigit() else target[k]
    last = keys[-1]
    if value is DELETE:
        del target[int(last) if last.isdigit() else last]
    else:
        target[int(last) if last.isdigit() else last] = value


DELETE = object()
MUTATIONS: list[tuple[str, dict[str, Any], str, str]] = [
    # (base payload, {path: value}, expected error field, expected code)
    ("SPA-VOLT-2026-884", {"header.valid_to": "2025-12-31"}, "payload.header.valid_to", "invalid_range"),
    ("SPA-VOLT-2026-884", {"header.valid_from": "2026-02-30"}, "payload.header.valid_from", "invalid_date"),
    ("SPA-VOLT-2026-884", {"header.valid_to": DELETE}, "payload.header.valid_to", "required"),
    ("SPA-VOLT-2026-884", {"header.manufacturer_code": "NOPE"}, "payload.header.manufacturer_code", "unresolved"),
    ("SPA-VOLT-2026-884", {"items": []}, "payload.items", "required"),
    ("SPA-VOLT-2026-884", {"items.0.deviated_cost": None}, "payload.items[0].deviated_cost", "required"),
    ("SPA-VOLT-2026-884", {"items.0.reference_cost": None}, "payload.items[0].reference_cost", "required"),
    ("SPA-VOLT-2026-884", {"items.0.deviated_cost": "-1.00"}, "payload.items[0].deviated_cost", "out_of_range"),
    ("SPA-VOLT-2026-884", {"items.0.deviated_cost": 78.0}, "payload.items[0].deviated_cost", "string_type"),
    ("SPA-VOLT-2026-884", {"items.0.deviated_cost": "1.1234567"}, "payload.items[0].deviated_cost",
     "too_many_decimals"),
    ("SPA-VOLT-2026-884", {"items.0.max_quantity": "0"}, "payload.items[0].max_quantity", "out_of_range"),
    ("SPA-VOLT-2026-884", {"items.1.sku": "BRK-3P-100A"}, "payload.items[1].sku", "duplicate"),
    ("SPA-VOLT-2026-884", {"items.0.sku": "NO-SUCH-SKU"}, "payload.items[0].sku", "unresolved"),
    ("SPA-VOLT-2026-884", {"items.0.bogus": "1"}, "payload.items[0].bogus", "unknown_field"),
    ("SPA-VOLT-2026-884", {"customers.0.customer_code": "NOBODY"}, "payload.customers[0].customer_code",
     "unresolved"),
    ("SPA-VOLT-2026-884", {"customers.1.customer_code": "ALDER"}, "payload.customers[1].customer_code", "duplicate"),
    ("SPA-VOLT-2026-884", {"customers": []}, "payload.customers", "required"),
    ("SPA-VOLT-2026-884", {"tiers": [{"tier_number": 1, "lower_bound": "0", "rate": "0.01"}]}, "payload.tiers",
     "not_applicable"),
    ("SPA-VOLT-2026-884", {"rules.quantity.cap_scope": "per_agreement"}, "payload.rules.quantity.cap_scope",
     "not_supported_until_release_2"),
    ("SPA-VOLT-2026-884", {"rules.adapter": "acme_special"}, "payload.rules.adapter", "unknown_adapter"),
    ("SPA-VOLT-2026-884", {"rules.date_basis": "order_date"}, "payload.rules.date_basis", "invalid_choice"),
    ("SPA-VOLT-2026-884", {"rules.rounding.scale": 7}, "payload.rules.rounding.scale", "less_than_equal"),
    ("SPA-VOLT-2026-884", {"header.agreement_type": "rebate"}, "payload.rules.type", "mismatch"),
    ("SPA-BRIT-2026-031", {"items.0.rate": None}, "payload.items[0].rate", "required"),
    ("SPA-BRIT-2026-031", {"items.0.rate": "1.5"}, "payload.items[0].rate", "out_of_range"),
    ("SPA-BRIT-2026-031", {"rules.calculation.cost_source": "agreement_reference"}, "payload.items[0].reference_cost",
     "required"),
    ("SPA-COPR-2026-107", {"items.0.flat_amount": None}, "payload.items[0].flat_amount", "required"),
    ("REB-VOLT-2026", {"tiers.0.lower_bound": "10"}, "payload.tiers[0].lower_bound", "invalid"),
    ("REB-VOLT-2026", {"tiers.2.lower_bound": "1000000"}, "payload.tiers[2].lower_bound", "not_increasing"),
    ("REB-VOLT-2026", {"tiers.1.tier_number": 3}, "payload.tiers[1].tier_number", "invalid"),
    ("REB-VOLT-2026", {"tiers.0.rate": "1.01"}, "payload.tiers[0].rate", "out_of_range"),
    ("REB-VOLT-2026", {"tiers.0.lower_bound": "0.001"}, "payload.tiers[0].lower_bound", "too_many_decimals"),
    ("REB-VOLT-2026", {"tiers": []}, "payload.tiers", "required"),
    ("REB-VOLT-2026", {"rules.tier_metric": "quantity"}, "payload.rules.tier_metric", "not_supported_until_release_2"),
    ("REB-NWND-2026", {"rules.tier_mode": "incremental"}, "payload.rules.spa_lines", "incompatible"),
    ("REB-VOLT-2026", {"rules.tier_mode": "incremental", "rules.spa_lines": "qualify_only"},
     "payload.rules.spa_lines", "incompatible"),  # whatever the volume_basis (purchases here)
    ("REB-VOLT-2026", {"rules.measurement_period.end": "2025-01-01"}, "payload.rules.measurement_period.end",
     "invalid_range"),
    ("REB-VOLT-2026", {"customers": [{"customer_code": "ALDER", "scope": "include"}]}, "payload.customers",
     "not_applicable"),
    ("REB-VOLT-2026", {"rules.product_scope.mode": "listed"}, "payload.items", "required"),
]  # fmt: skip


@pytest.fixture(scope="module")
def scratch(org: Org) -> dict[str, Any]:
    """One draft re-used for every mutation (each PUT is a new revision)."""
    return create(org.maker, payload=fresh("SPA-VOLT-2026-884"))


@pytest.mark.parametrize(("base", "changes", "field", "code"), MUTATIONS, ids=lambda x: str(x)[:40])
def test_validator_rejects_mutated_payload_precisely(
    org: Org, scratch: dict[str, Any], base: str, changes: dict[str, Any], field: str, code: str
) -> None:
    p = fresh(base)
    for path, value in changes.items():
        _set(p, path, value)
    r = update(org.maker, scratch, p)
    assert r.status_code == 200, r.text
    scratch.update(r.json())
    validation = r.json()["validation"]
    assert not validation["valid"]
    assert (field, code) in codes(validation), validation["errors"]


def test_unresolved_references_are_reported_and_block_submit(org: Org) -> None:
    p = fresh("SPA-VOLT-2026-884")
    p["items"][0]["sku"], p["customers"][0]["customer_code"] = "GHOST-1", "GHOSTCO"
    d = create(org.maker, payload=p)
    assert d["validation"]["references"] == {
        "unresolved_customers": ["GHOSTCO"],
        "unresolved_skus": ["GHOST-1"],
    }
    r = submit(org.maker, d["id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert {
        "field": "payload.items[0].sku",
        "code": "unresolved",
        "message": "Unknown SKU GHOST-1.",
    } in r.json()["error"]["details"]


def test_positive_variants_are_accepted(org: Org) -> None:
    """Rules that must *not* fire: reference_cost optional when the cost comes from the line; adjacent
    tiers strictly increasing; customer scope 'all' without a list."""
    spa = fresh("SPA-VOLT-2026-884")
    spa["rules"]["calculation"]["cost_source"] = "line_cost"
    for item in spa["items"]:
        item.pop("reference_cost")
    spa["rules"]["customer_scope"]["mode"], spa["customers"] = "all", []
    assert create(org.maker, payload=spa)["validation"]["valid"]
    reb = fresh("REB-VOLT-2026")
    reb["tiers"][1]["lower_bound"] = "0.01"  # just above tier 1
    assert create(org.maker, payload=reb)["validation"]["valid"]


def test_warnings_must_be_acknowledged(org: Org) -> None:
    p = fresh("REB-VOLT-2026")
    p["rules"]["measurement_period"]["end"] = (
        "2026-06-30"  # differs from the validity: a warning, not an error
    )
    d = create(org.maker, payload=p)
    assert d["validation"]["valid"]
    assert codes(d["validation"], "warnings") == {
        ("payload.rules.measurement_period", "measurement_period_mismatch")
    }
    r = submit(org.maker, d["id"])
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "unacknowledged_warning"
    assert submit(org.maker, d["id"], [d["validation"]["warnings"][0]["id"]]).status_code == 200


def test_new_agreement_with_an_existing_number_is_rejected(org: Org) -> None:
    p = fresh("SPA-COPR-2026-107")
    activate(org.maker, org.p1, p)
    d = create(org.maker, payload=p)
    assert ("payload.header.external_number", "agreement_exists") in codes(d["validation"])


# ---- edits, If-Match, return/reject, four-eyes ------------------------------------------------------------


def test_put_requires_the_current_revision_and_keeps_revisions(org: Org, admin_engine: Engine) -> None:
    d = create(org.maker, payload=fresh("SPA-VOLT-2026-901"))
    p = d["payload"]
    assert (
        org.maker.put(f"/agreement-drafts/{d['id']}", json={"payload": p}).status_code == 412
    )  # no If-Match
    r = update(org.maker, d, p)
    assert r.status_code == 200 and r.json()["revision"] == 2 and r.headers["ETag"] == '"2"'
    stale = update(org.maker, d, p)  # still revision 1
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "precondition_failed"
    with admin_engine.connect() as conn:
        revs = conn.execute(
            text("SELECT revision FROM agreement_draft_revisions WHERE draft_id = :d ORDER BY revision"),
            {"d": d["id"]},
        ).scalars()
        assert list(revs) == [1, 2]


def test_return_then_reject(org: Org) -> None:
    d = create(org.maker, payload=fresh("SPA-VOLT-2026-901"))
    assert submit(org.maker, d["id"]).status_code == 200
    pending = update(org.maker, {**d, "revision": 1}, d["payload"])
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "invalid_state"
    r = org.p1.post(f"/agreement-drafts/{d['id']}/return", json={"comment": "Check the sell price."})
    assert r.status_code == 200 and (r.json()["status"], r.json()["review_comment"]) == (
        "draft",
        "Check the sell price.",
    )
    assert update(org.maker, r.json(), d["payload"]).status_code == 200
    assert submit(org.maker, d["id"]).status_code == 200
    r = org.p1.post(f"/agreement-drafts/{d['id']}/reject", json={"reason": "Duplicate of SPA 884."})
    assert r.status_code == 200 and (r.json()["status"], r.json()["rejection_reason"]) == (
        "rejected",
        "Duplicate of SPA 884.",
    )
    assert approve(org.p1, d["id"]).status_code == 409


def test_four_eyes_maker_cannot_approve_and_the_attempt_is_audited(org: Org, admin_engine: Engine) -> None:
    d = create(org.maker, payload=fresh("SPA-VOLT-2026-901"))
    assert submit(org.maker, d["id"]).status_code == 200
    r = approve(org.maker, d["id"])  # the admin created the draft
    assert r.status_code == 403 and r.json()["error"]["code"] == "four_eyes_violation"
    # an approver who edited the draft is a maker too
    returned = org.p1.post(f"/agreement-drafts/{d['id']}/return", json={"comment": "fix"}).json()
    assert update(org.p1, returned, d["payload"]).status_code == 200
    assert submit(org.p1, d["id"]).status_code == 200
    assert approve(org.p1, d["id"]).json()["error"]["code"] == "four_eyes_violation"
    with admin_engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE entity_id = :d AND action = 'agreement_draft.four_eyes_violation'"
            ),
            {"d": d["id"]},
        ).scalar_one()
    assert n == 2
    assert approve(org.p2, d["id"]).status_code == 200


def test_two_parallel_approvals_exactly_one_wins(org: Org, admin_engine: Engine) -> None:
    d = create(org.maker, payload=fresh("SPA-BRIT-2026-031"))
    assert submit(org.maker, d["id"]).status_code == 200
    barrier, results = threading.Barrier(2), {}

    def go(name: str, api: Api) -> None:
        barrier.wait()
        results[name] = approve(api, d["id"])

    threads = [threading.Thread(target=go, args=(n, a)) for n, a in (("p1", org.p1), ("p2", org.p2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(r.status_code for r in results.values())
    assert statuses == [200, 409], {k: r.text for k, r in results.items()}
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM agreement_versions WHERE draft_id = :d"), {"d": d["id"]}
        ).scalar_one()
    assert n == 1


# ---- split-supersede, overlap, diff, cancel -----------------------------------------------------------------


def _versions(api: Api, agreement_id: str) -> dict[int, dict[str, Any]]:
    rows = api.get(f"/agreements/{agreement_id}/versions").json()["data"]
    return {v["version_number"]: v for v in rows}


def test_mid_period_amendment_splits_v1_into_v1a_and_v2(org: Org) -> None:
    """BUSINESS_LOGIC.md §9.2: v1 Jan-Dec; amendment from Jul 1 with new terms."""
    v1 = activate(org.maker, org.p1, fresh("SPA-VOLT-2026-884"))
    d = create(org.maker, amends_version_id=v1["id"])  # payload defaults to v1's terms
    assert d["agreement_id"] == v1["agreement_id"] and d["validation"]["valid"], d["validation"]
    p = d["payload"]
    p["header"]["valid_from"] = "2026-07-01"
    brk = next(i for i in p["items"] if i["sku"] == "BRK-3P-100A")
    brk["deviated_cost"] = "80.00"
    d = update(org.maker, d, p).json()
    assert d["validation"]["valid"], d["validation"]
    assert submit(org.maker, d["id"]).status_code == 200
    r = approve(org.p1, d["id"])
    assert r.status_code == 200, r.text

    vs = _versions(org.p1, v1["agreement_id"])
    old, v1a, v2 = vs[1], vs[2], vs[3]
    assert old["id"] == v1["id"] and old["status"] == "superseded" and old["superseded_at"] is not None
    assert old["superseded_by_version_id"] == v2["id"] == r.json()["resulting_version_id"]
    assert (v1a["status"], v1a["valid_from"], v1a["valid_to"]) == ("active", "2026-01-01", "2026-06-30")
    assert v1a["derived_from_version_id"] == v1["id"] and v1a["content_hash"] == v1["content_hash"]
    assert (v2["status"], v2["valid_from"], v2["valid_to"]) == ("active", "2026-07-01", "2026-12-31")
    assert v2["supersedes_version_id"] == v1["id"] and v2["content_hash"] != v1["content_hash"]
    old_items = {i["sku"]: i["deviated_cost"] for i in items_of(org.p1, v1a["id"])}
    new_items = {i["sku"]: i["deviated_cost"] for i in items_of(org.p1, v2["id"])}
    assert Decimal(old_items["BRK-3P-100A"]) == Decimal("78.00")
    assert Decimal(new_items["BRK-3P-100A"]) == Decimal("80.00")
    assert {k: Decimal(v) for k, v in old_items.items() if k != "BRK-3P-100A"} == {
        k: Decimal(v) for k, v in new_items.items() if k != "BRK-3P-100A"
    }

    diff = org.p2.get(f"/agreement-versions/{v1a['id']}/diff/{v2['id']}").json()["changes"]
    assert {c["path"]: (c["before"], c["after"]) for c in diff} == {
        "header.valid_from": ("2026-01-01", "2026-07-01"),
        "header.valid_to": ("2026-06-30", "2026-12-31"),
        "items[BRK-3P-100A].deviated_cost": ("78", "80"),
    }
    assert org.p2.get(f"/agreement-versions/{v1['id']}/diff/{v1a['id']}").json()["changes"] == [
        {"path": "header.valid_to", "before": "2026-12-31", "after": "2026-06-30"}
    ]
    again = create(org.maker, amends_version_id=v1["id"])  # v1 is superseded now
    assert ("amends_version_id", "stale_version") in codes(again["validation"])


def test_inner_amendment_leaves_pieces_on_both_sides(org: Org) -> None:
    v1 = activate(org.maker, org.p1, fresh("SPA-VOLT-2026-901"))
    d = create(org.maker, amends_version_id=v1["id"])
    p = d["payload"]
    p["header"]["valid_from"], p["header"]["valid_to"] = "2026-04-01", "2026-06-30"
    p["items"][0]["deviated_cost"] = "79.00"
    d = update(org.maker, d, p).json()
    assert submit(org.maker, d["id"]).status_code == 200 and approve(org.p2, d["id"]).status_code == 200
    vs = _versions(org.p1, v1["agreement_id"])
    spans = {n: (v["status"], v["valid_from"], v["valid_to"]) for n, v in vs.items()}
    assert spans == {
        1: ("superseded", "2026-01-01", "2026-12-31"),
        2: ("expired", "2026-01-01", "2026-03-31"),  # before the business date 2026-06-15
        3: ("active", "2026-04-01", "2026-06-30"),
        4: ("active", "2026-07-01", "2026-12-31"),
    }
    assert vs[2]["derived_from_version_id"] == vs[4]["derived_from_version_id"] == v1["id"]
    assert vs[2]["content_hash"] == vs[4]["content_hash"] == v1["content_hash"]


def test_overlapping_new_version_of_the_same_agreement_is_rejected(org: Org) -> None:
    v1 = activate(org.maker, org.p1, fresh("REB-VOLT-2026"))
    p = payload("REB-VOLT-2026")
    p["header"]["external_number"] = v1["external_number"]
    p["header"]["valid_from"] = "2026-12-01"  # overlaps v1 (Jan-Dec) without amending it
    p["header"]["valid_to"] = p["rules"]["measurement_period"]["end"] = "2027-11-30"
    p["rules"]["measurement_period"]["start"] = "2026-12-01"
    d = create(org.maker, payload=p, agreement_id=v1["agreement_id"])
    assert ("payload.header.valid_from", "version_overlap") in codes(d["validation"])
    p["header"]["valid_from"] = p["rules"]["measurement_period"]["start"] = "2027-01-01"  # adjacent: allowed
    p["header"]["valid_to"] = p["rules"]["measurement_period"]["end"] = "2027-12-31"
    d = update(org.maker, d, p).json()
    assert d["validation"]["valid"], d["validation"]
    assert submit(org.maker, d["id"]).status_code == 200 and approve(org.p1, d["id"]).status_code == 200
    assert [v["status"] for v in _versions(org.p1, v1["agreement_id"]).values()] == ["active", "active"]


def test_cancel_going_forward(org: Org) -> None:
    v1 = activate(org.maker, org.p1, fresh("SPA-BRIT-2026-031"))  # 2026-03-01 .. 2026-08-31
    r = org.p1.post(
        f"/agreements/{v1['agreement_id']}/cancel",
        json={"effective_date": "2026-06-01", "reason": "Ended early."},
    )
    assert r.status_code == 200 and r.json()["cancelled_at"] is not None
    vs = _versions(org.p1, v1["agreement_id"])
    assert (vs[1]["status"], vs[2]["status"], vs[2]["valid_from"], vs[2]["valid_to"]) == (
        "superseded",
        "expired",  # ends 2026-05-31, before the business date 2026-11-11
        "2026-03-01",
        "2026-05-31",
    )
    d = create(org.maker, amends_version_id=vs[2]["id"])
    assert ("agreement_id", "agreement_cancelled") in codes(d["validation"])
    again = org.p1.post(
        f"/agreements/{v1['agreement_id']}/cancel", json={"effective_date": "2026-06-01", "reason": "x"}
    )
    assert again.status_code == 409


# ---- SPA items from the spreadsheet template (S4.4) ---------------------------------------------------------


def test_brightline_items_by_template_match_the_manual_payload(org: Org) -> None:
    manual = fresh("SPA-BRIT-2026-031")
    fid = clean_file(org.ws, "SYNTHETIC_spa_brightline_items.xlsx",
                     data("agreements/SYNTHETIC_spa_brightline_items.xlsx"), XLSX)  # fmt: skip
    d = create(org.maker, source="spreadsheet_template", source_file_id=fid, payload={**manual, "items": []})
    assert ("payload.items", "required") in codes(d["validation"])
    path, match = f"/agreement-drafts/{d['id']}/items:import", {"If-Match": '"1"'}
    body = {"file_id": fid, "rate_format": "percent"}
    assert org.maker.post(path, json=body).status_code == 412  # If-Match required

    bad = org.maker.post(
        path, json={**body, "rate_format": "fraction"}, headers=match
    )  # "12" is not a fraction
    assert bad.status_code == 422
    assert {(e["field"], e["code"]) for e in bad.json()["error"]["details"]} == {
        ("row 5.Margin %", "out_of_range"),
        ("row 6.Margin %", "out_of_range"),
    }
    dry = org.maker.post(path, json={**body, "dry_run": True}, headers=match).json()
    assert dry["dry_run"] and dry["rows"] == 2 and dry["draft"] is None
    assert org.maker.get(f"/agreement-drafts/{d['id']}").json()["revision"] == 1

    r = org.maker.post(path, json=body, headers=match)
    assert r.status_code == 200, r.text
    d = r.json()["draft"]
    assert d["revision"] == 2 and d["validation"]["valid"], d["validation"]
    got = {(i["sku"], Decimal(i["rate"]), i["claim_uom"]) for i in d["payload"]["items"]}
    assert got == {(i["sku"], Decimal(i["rate"]), "EA") for i in manual["items"]}  # sheet: Approved UoM = EA
    assert submit(org.maker, d["id"]).status_code == 200
    r = approve(org.p2, d["id"])
    assert r.status_code == 200, r.text
    v = org.p2.get(f"/agreement-versions/{r.json()['resulting_version_id']}").json()
    assert v["source"] == "spreadsheet_template" and v["source_file_id"] == fid and v["item_count"] == 2
    assert {(i["sku"], Decimal(i["rate"])) for i in items_of(org.p2, v["id"])} == {
        ("LED-2X4-40W", Decimal("0.12")),
        ("LED-HB-150W", Decimal("0.12")),
    }


# ---- expiry sweep (S4.6) ----------------------------------------------------------------------------------


def test_expiry_sweep_alerts_then_expires(migrated_db: DbUrls, admin_engine: Engine) -> None:
    app: FastAPI = make_app(migrated_db.app)
    with fake_clock() as fc, api_client(app) as api:
        fc.t = datetime(2026, 11, 11, 15, tzinfo=UTC)
        ws = new_workspace(app, api, master=True)
        etag = api.get("/settings").headers["ETag"]
        assert (
            api.patch("/settings", json={"four_eyes_required": False}, headers={"If-Match": etag}).status_code
            == 200
        )
        p = payload("SPA-VOLT-2026-901")
        p["header"]["valid_to"] = "2026-11-30"  # 19 days left on 2026-11-11: inside the 30-day window
        v = activate(api, api, p)
        drain(app)  # first sweep (enqueued by the approval) runs now
        fc.t = datetime(2026, 12, 3, 15, tzinfo=UTC)  # next sweep is due; 2026-11-30 has passed
        drain(app)
    with admin_engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM agreement_versions WHERE id = :v"), {"v": v["id"]}
        ).scalar_one()
        assert status == "expired"
        actions = list(
            conn.execute(
                text("SELECT action FROM audit_log WHERE tenant_id = :t AND entity_id = :v ORDER BY id"),
                {"t": ws.tenant_id, "v": v["id"]},
            ).scalars()
        )
    assert actions == [
        "agreement_version.activated",
        "agreement_version.expiry_alert",
        "agreement_version.expired",
    ]


# ---- database invariants: exclusion constraint and immutability (direct SQL) ------------------------------


@contextmanager
def _sql(engine: Engine, tenant: str) -> Iterator[Connection]:
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
            yield conn
        finally:
            tx.rollback()


def _error(conn: Connection, sql: str, params: dict[str, Any]) -> str | None:
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(sql), params)
    except DBAPIError as exc:
        savepoint.rollback()
        return str(exc.orig).splitlines()[0]
    savepoint.rollback()
    return None


COPY_VERSION = (
    "INSERT INTO agreement_versions (tenant_id, id, agreement_id, version_number, status, valid_from, valid_to, rules, "
    "rules_schema_version, priority, source, content_hash, created_by, approved_by, activated_at, created_txid) "
    "SELECT tenant_id, gen_random_uuid(), agreement_id, :n, :status, :f, :t, rules, rules_schema_version, priority, "
    "source, content_hash, created_by, approved_by, now(), 0 FROM agreement_versions WHERE id = :v"
)


def test_exclusion_constraint_rejects_overlap_and_allows_adjacency(org: Org, app_engine: Engine) -> None:
    v = activate(org.maker, org.p1, fresh("SPA-VOLT-2026-884"))  # 2026-01-01 .. 2026-12-31
    with _sql(app_engine, org.ws.tenant_id) as conn:

        def copy(n: int, status: str, f: str, t: str) -> str | None:
            return _error(conn, COPY_VERSION, {"n": n, "status": status, "f": f, "t": t, "v": v["id"]})

        assert "ex_agreement_versions_no_overlap" in (copy(90, "active", "2026-12-31", "2027-03-31") or "")
        assert "ex_agreement_versions_no_overlap" in (copy(91, "expired", "2026-06-01", "2026-06-30") or "")
        assert copy(92, "active", "2027-01-01", "2027-12-31") is None  # adjacent: allowed
        assert "created active or expired" in (copy(93, "superseded", "2027-01-01", "2027-12-31") or "")
        conn.execute(
            text("UPDATE agreement_versions SET status = 'superseded', superseded_at = now() WHERE id = :v"),
            {"v": v["id"]},
        )
        assert copy(94, "active", "2026-03-01", "2026-04-30") is None  # superseded versions do not govern


IMMUTABLE: list[tuple[str, str, str]] = [
    # (statement, error expected for rebate_app, error expected for the owner role)
    ("UPDATE agreement_versions SET valid_to = '2027-06-30' WHERE id = :v", "permission denied", "immutable"),
    ("UPDATE agreement_versions SET rules = '{}' WHERE id = :v", "permission denied", "immutable"),
    ("UPDATE agreement_versions SET content_hash = repeat('0', 64) WHERE id = :v", "permission denied", "immutable"),
    ("UPDATE agreement_versions SET status = 'cancelled' WHERE id = :v; "
     "UPDATE agreement_versions SET status = 'active' WHERE id = :v", "cannot become", "cannot become"),
    ("DELETE FROM agreement_versions WHERE id = :v", "permission denied", "append-only"),
    ("UPDATE agreement_versions SET superseded_by_version_id = id WHERE id = :v", "ck_agreement_versions_superseded_by",
     "ck_agreement_versions_superseded_by"),
    ("UPDATE agreement_version_items SET deviated_cost = 1 WHERE version_id = :v", "permission denied", "append-only"),
    ("DELETE FROM agreement_version_items WHERE version_id = :v", "permission denied", "append-only"),
    ("INSERT INTO agreement_version_items (tenant_id, id, version_id, line_number, product_id, deviated_cost) "
     "SELECT tenant_id, gen_random_uuid(), version_id, 99, (SELECT id FROM products WHERE sku = 'PNL-400A'), 1 "
     "FROM agreement_version_items WHERE version_id = :v LIMIT 1", "immutable", "immutable"),
    ("INSERT INTO agreement_version_customers (tenant_id, version_id, customer_id, scope) "
     "SELECT tenant_id, id, (SELECT id FROM customers WHERE code = 'FOXHL'), 'include' "
     "FROM agreement_versions WHERE id = :v", "immutable", "immutable"),
    ("UPDATE agreement_version_customers SET scope = 'exclude' WHERE version_id = :v", "permission denied",
     "append-only"),
    ("UPDATE agreement_draft_revisions SET change_summary = 'x' WHERE draft_id = :d", "permission denied",
     "append-only"),
    ("DELETE FROM agreement_drafts WHERE id = :d", "permission denied", "violates foreign key"),
]  # fmt: skip


@pytest.mark.parametrize("role", ["app", "owner"])
def test_active_versions_cannot_be_altered_even_with_direct_sql(
    org: Org, app_engine: Engine, owner_engine: Engine, role: str
) -> None:
    v = activate(org.maker, org.p1, fresh("SPA-VOLT-2026-884"))
    engine = app_engine if role == "app" else owner_engine
    with _sql(engine, org.ws.tenant_id) as conn:
        for statement, app_error, owner_error in IMMUTABLE:
            expected = app_error if role == "app" else owner_error
            savepoint = conn.begin_nested()
            got = None
            try:
                for part in statement.split("; "):
                    conn.execute(text(part), {"v": v["id"], "d": v["draft_id"]})
            except DBAPIError as exc:
                got = str(exc.orig).splitlines()[0]
            savepoint.rollback()
            assert got is not None and expected in got, (statement, got)


# ---- races on the agreement (review H1): the approval locks and re-reads before validating -----------------


def _wait_for_lock_waiter(engine: Engine, timeout: float = 10.0) -> None:

    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            ).scalar_one()
            conn.rollback()
            if waiting:
                return
            time.sleep(0.05)
    raise AssertionError("the approval never waited for the agreement lock")


def test_cancel_committed_while_an_approval_waits_makes_the_approval_fail(
    org: Org, app_engine: Engine, admin_engine: Engine
) -> None:
    v1 = activate(org.maker, org.p1, fresh("SPA-VOLT-2026-884"))
    d = create(org.maker, amends_version_id=v1["id"])
    p = d["payload"]
    p["header"]["valid_from"] = "2026-07-01"
    d = update(org.maker, d, p).json()
    assert submit(org.maker, d["id"]).status_code == 200
    result: dict[str, Any] = {}
    with (
        app_engine.connect() as conn
    ):  # a concurrent cancel: holds the agreement row, then commits cancelled_at
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org.ws.tenant_id})
        conn.execute(text("SELECT 1 FROM agreements WHERE id = :a FOR UPDATE"), {"a": v1["agreement_id"]})
        etag = org.p1.get(f"/agreement-drafts/{d['id']}").headers["ETag"]
        t = threading.Thread(target=lambda: result.update(r=approve(org.p1, d["id"], revision=etag)))
        t.start()
        _wait_for_lock_waiter(admin_engine)
        conn.execute(
            text("UPDATE agreements SET cancelled_at = now() WHERE id = :a"), {"a": v1["agreement_id"]}
        )
        conn.commit()
    t.join()
    assert result["r"].status_code == 409 and result["r"].json()["error"]["code"] == "invalid_state", result[
        "r"
    ].text
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM agreement_versions WHERE draft_id = :d"), {"d": d["id"]}
        ).scalar_one()
    assert n == 0


def test_two_amendments_of_one_version_approved_in_parallel_exactly_one_wins(
    org: Org, admin_engine: Engine
) -> None:
    v1 = activate(org.maker, org.p1, fresh("SPA-VOLT-2026-884"))
    drafts = []
    for start in ("2026-07-01", "2026-09-01"):
        d = create(org.maker, amends_version_id=v1["id"])
        p = d["payload"]
        p["header"]["valid_from"] = start
        d = update(org.maker, d, p).json()
        assert submit(org.maker, d["id"]).status_code == 200
        drafts.append(d)
    barrier, results = threading.Barrier(2), {}

    def go(name: str, api: Api, draft: dict[str, Any]) -> None:
        etag = api.get(f"/agreement-drafts/{draft['id']}").headers["ETag"]
        barrier.wait()
        results[name] = approve(api, draft["id"], revision=etag)

    threads = [
        threading.Thread(target=go, args=(n, a, dr))
        for n, a, dr in (("p1", org.p1, drafts[0]), ("p2", org.p2, drafts[1]))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for r in results.values()) == [200, 409], {
        k: r.text for k, r in results.items()
    }
    loser = next(r for r in results.values() if r.status_code == 409)
    assert loser.json()["error"]["code"] == "invalid_state"
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, superseded_by_version_id FROM agreement_versions WHERE id = :v"),
            {"v": v1["id"]},
        ).one()
    assert rows.status == "superseded" and rows.superseded_by_version_id is not None
