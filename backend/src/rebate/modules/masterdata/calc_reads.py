"""Narrow, read-only master-data queries for the calculation runs (``claims`` module). Set-based, tenant-scoped."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def manufacturer_ids(db: Session, tenant_id: UUID) -> list[UUID]:
    return list(
        db.scalars(text("SELECT id FROM manufacturers WHERE tenant_id = :t ORDER BY id"), {"t": tenant_id})
    )


def existing_manufacturer_ids(db: Session, tenant_id: UUID, ids: list[UUID]) -> set[UUID]:
    return set(
        db.scalars(
            text("SELECT id FROM manufacturers WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"),
            {"t": tenant_id, "ids": ids},
        )
    )


def manufacturer_settings_rows(db: Session, tenant_id: UUID, manufacturer_ids_: list[UUID]) -> list[Any]:
    """Claim-window and conflict settings (columns: manufacturer_id, claim_window_basis, claim_window_days,
    allow_negative_claims, conflict_policy); empty ``manufacturer_ids_`` = all manufacturers."""
    return list(
        db.execute(
            text(
                "SELECT manufacturer_id, claim_window_basis, claim_window_days, allow_negative_claims, "
                "conflict_policy FROM manufacturer_settings WHERE tenant_id = :t "
                "AND (cardinality(CAST(:m AS uuid[])) = 0 OR manufacturer_id = ANY(CAST(:m AS uuid[]))) "
                "ORDER BY manufacturer_id"
            ),
            {"t": tenant_id, "m": manufacturer_ids_},
        )
    )


def uom_conversion_rows(db: Session, tenant_id: UUID) -> list[Any]:
    """Columns: product_id (None = tenant default), from_uom, to_uom, factor."""
    return list(
        db.execute(
            text(
                "SELECT product_id, from_uom, to_uom, factor FROM uom_conversions WHERE tenant_id = :t ORDER BY id"
            ),
            {"t": tenant_id},
        )
    )


def customer_parent_rows(db: Session, tenant_id: UUID) -> list[Any]:
    """Columns: id, parent_customer_id."""
    return list(
        db.execute(
            text("SELECT id, parent_customer_id FROM customers WHERE tenant_id = :t"), {"t": tenant_id}
        )
    )
