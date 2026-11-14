"""Narrow, read-only agreement queries for the calculation runs (``claims`` module)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class VersionsInForce:
    """Rows of the versions in force at an instant, with their items, customers and tiers (raw rows)."""

    versions: list[
        Any
    ]  # id, agreement_id, agreement_type, external_number, name, manufacturer_id, valid_from,
    # valid_to, priority, rules, content_hash
    items: dict[UUID, list[Any]] = field(
        default_factory=dict
    )  # product_id, reference_cost, deviated_cost, ...
    customers: dict[UUID, list[Any]] = field(default_factory=dict)  # customer_id, scope
    tiers: dict[UUID, list[Any]] = field(default_factory=dict)  # tier_number, lower_bound, rate


def versions_in_force(
    db: Session, tenant_id: UUID, as_of: datetime, manufacturer_ids: list[UUID]
) -> VersionsInForce:
    """Versions activated at or before ``as_of`` and not yet superseded / cancelled then (expired versions still
    govern their period). Empty ``manufacturer_ids`` = all manufacturers. Items with a customer are skipped."""
    rows = list(
        db.execute(
            text(
                "SELECT v.id, v.agreement_id, a.agreement_type, a.external_number, a.name, a.manufacturer_id, "
                "v.valid_from, v.valid_to, v.priority, v.rules, v.content_hash "
                "FROM agreement_versions v JOIN agreements a ON a.tenant_id = v.tenant_id AND a.id = v.agreement_id "
                "WHERE v.tenant_id = :t AND v.activated_at <= :as_of "
                "AND (v.status IN ('active', 'expired') "
                "     OR (v.status = 'superseded' AND v.superseded_at > :as_of) "
                "     OR (v.status = 'cancelled' AND a.cancelled_at > :as_of)) "
                "AND (cardinality(CAST(:m AS uuid[])) = 0 OR a.manufacturer_id = ANY(CAST(:m AS uuid[]))) "
                "ORDER BY v.id"
            ),
            {"t": tenant_id, "as_of": as_of, "m": manufacturer_ids},
        )
    )
    out = VersionsInForce(rows)
    ids = [r.id for r in rows]
    params = {"t": tenant_id, "ids": ids}
    for i in db.execute(
        text(
            "SELECT version_id, product_id, reference_cost, deviated_cost, sell_price, rate, flat_amount, "
            "max_quantity, claim_uom FROM agreement_version_items "
            "WHERE tenant_id = :t AND version_id = ANY(CAST(:ids AS uuid[])) AND customer_id IS NULL "
            "ORDER BY version_id, line_number"
        ),
        params,
    ):
        out.items.setdefault(i.version_id, []).append(i)
    for c in db.execute(
        text(
            "SELECT version_id, customer_id, scope FROM agreement_version_customers "
            "WHERE tenant_id = :t AND version_id = ANY(CAST(:ids AS uuid[])) ORDER BY version_id, customer_id"
        ),
        params,
    ):
        out.customers.setdefault(c.version_id, []).append(c)
    for t in db.execute(
        text(
            "SELECT version_id, tier_number, lower_bound, rate FROM agreement_version_tiers "
            "WHERE tenant_id = :t AND version_id = ANY(CAST(:ids AS uuid[])) ORDER BY version_id, tier_number"
        ),
        params,
    ):
        out.tiers.setdefault(t.version_id, []).append(t)
    return out


def draft_for_preview(db: Session, tenant_id: UUID, draft_id: UUID) -> Any | None:
    """Columns: payload, agreement_id, amends_version_id."""
    return db.execute(
        text(
            "SELECT payload, agreement_id, amends_version_id FROM agreement_drafts WHERE tenant_id = :t AND id = :id"
        ),
        {"t": tenant_id, "id": draft_id},
    ).first()


def version_rules(db: Session, tenant_id: UUID, version_ids: list[UUID]) -> dict[UUID, Any]:
    """Stored rules of the given versions (any status): a claim keeps the terms it was calculated under."""
    return {
        r.id: r.rules
        for r in db.execute(
            text(
                "SELECT id, rules FROM agreement_versions WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"t": tenant_id, "ids": version_ids},
        )
    }
