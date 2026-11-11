"""Loaders: database rows -> frozen ``rebate_engine`` inputs (S5.3).

Read-only SQL over the source tables of other modules (sales/purchase lines, agreement versions, master data):
the engine needs set-based bulk reads that no module service offers, and routing 1M lines through ORM objects
would defeat the point. Every query is tenant-scoped explicitly and by RLS.

Reproducibility: agreement versions are visible as of the run's ``as_of`` (activated at or before it and not yet
superseded/cancelled then); source lines are those of import batches completed at or before ``data_cutoff``.
Source lines superseded by a later import are NOT reconstructed (the current version is used); rerunning after a
source change therefore yields new inputs and a different ``input_checksum`` by design.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.modules.claims import frozen
from rebate_engine.models import (
    CustomerScope,
    EngineRuleError,
    ManufacturerSettings,
    RebateVersion,
    ReturnOriginal,
    SalesLine,
    SpaItem,
    SpaVersion,
    Tier,
    UomConversion,
    VolumeLine,
)
from rebate_engine.rules import parse_rebate_rules, parse_spa_rules


class VersionRejected(Exception):
    """A stored version the engine cannot evaluate; ``error`` is the engine's ``EngineRuleError``."""

    def __init__(self, external_number: str, error: EngineRuleError) -> None:
        super().__init__(f"{external_number}: {error}")
        self.external_number, self.error = external_number, error


def _parsed[T](external_number: str, parse: Callable[[Any], T], rules: Any) -> T:
    try:
        return parse(rules)
    except EngineRuleError as exc:
        raise VersionRejected(external_number, exc) from exc


@dataclass(frozen=True)
class RebateEntry:
    """A rebate version with the agreement it belongs to (the ledger is keyed by agreement + period)."""

    version: RebateVersion
    agreement_id: UUID
    valid_from: date
    valid_to: date
    content_hash: str


@dataclass(frozen=True)
class Versions:
    spa: tuple[SpaVersion, ...]
    spa_hashes: dict[str, str]
    rebates: tuple[RebateEntry, ...]

    @property
    def ids(self) -> list[UUID]:
        return sorted(UUID(v.version_id) for v in self.spa) + sorted(
            UUID(r.version.version_id) for r in self.rebates
        )


def _ids(values: list[UUID]) -> dict[str, Any]:
    return {"ids": values}


def load_settings(
    db: Session, tenant_id: UUID, manufacturer_ids: list[UUID]
) -> tuple[ManufacturerSettings, ...]:
    rows = db.execute(
        text(
            "SELECT manufacturer_id, claim_window_basis, claim_window_days, allow_negative_claims, conflict_policy "
            "FROM manufacturer_settings WHERE tenant_id = :t "
            "AND (cardinality(CAST(:m AS uuid[])) = 0 OR manufacturer_id = ANY(CAST(:m AS uuid[]))) "
            "ORDER BY manufacturer_id"
        ),
        {"t": tenant_id, "m": manufacturer_ids},
    )
    return tuple(
        ManufacturerSettings(
            manufacturer_id=str(r.manufacturer_id),
            conflict_policy=r.conflict_policy,
            claim_window_basis=r.claim_window_basis,
            claim_window_days=r.claim_window_days,
            allow_negative_claims=r.allow_negative_claims,
        )
        for r in rows
    )


def load_conversions(db: Session, tenant_id: UUID) -> tuple[UomConversion, ...]:
    rows = db.execute(
        text(
            "SELECT product_id, from_uom, to_uom, factor FROM uom_conversions WHERE tenant_id = :t "
            "ORDER BY id"
        ),
        {"t": tenant_id},
    )
    return tuple(
        UomConversion(
            product_id=None if r.product_id is None else str(r.product_id),
            from_uom=r.from_uom,
            to_uom=r.to_uom,
            factor=r.factor,
        )
        for r in rows
    )


def load_manufacturer_ids(db: Session, tenant_id: UUID) -> list[UUID]:
    return list(
        db.scalars(text("SELECT id FROM manufacturers WHERE tenant_id = :t ORDER BY id"), {"t": tenant_id})
    )


def _version_rows(db: Session, tenant_id: UUID, as_of: datetime, manufacturer_ids: list[UUID]) -> list[Any]:
    return list(
        db.execute(
            text(
                "SELECT v.id, v.agreement_id, a.agreement_type, a.external_number, a.manufacturer_id, "
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


def load_versions(db: Session, tenant_id: UUID, as_of: datetime, manufacturer_ids: list[UUID]) -> Versions:
    """Agreement versions in force at ``as_of``. Raises the engine's ``RulesError`` (``UnsupportedRuleError`` for the
    Release-2 rules) for a stored version it cannot evaluate; ``external_number`` is in the message."""
    rows = _version_rows(db, tenant_id, as_of, manufacturer_ids)
    version_ids = [r.id for r in rows]
    items = db.execute(
        text(
            "SELECT version_id, product_id, reference_cost, deviated_cost, sell_price, rate, flat_amount, "
            "max_quantity, claim_uom FROM agreement_version_items "
            "WHERE tenant_id = :t AND version_id = ANY(CAST(:ids AS uuid[])) AND customer_id IS NULL "
            "ORDER BY version_id, line_number"
        ),
        {"t": tenant_id, **_ids(version_ids)},
    )
    items_by: dict[UUID, list[Any]] = {}
    for i in items:
        items_by.setdefault(i.version_id, []).append(i)
    customers_by: dict[UUID, list[CustomerScope]] = {}
    for c in db.execute(
        text(
            "SELECT version_id, customer_id, scope FROM agreement_version_customers "
            "WHERE tenant_id = :t AND version_id = ANY(CAST(:ids AS uuid[])) ORDER BY version_id, customer_id"
        ),
        {"t": tenant_id, **_ids(version_ids)},
    ):
        customers_by.setdefault(c.version_id, []).append(
            CustomerScope(customer_id=str(c.customer_id), scope=c.scope)
        )
    tiers_by: dict[UUID, list[Tier]] = {}
    for t in db.execute(
        text(
            "SELECT version_id, tier_number, lower_bound, rate FROM agreement_version_tiers "
            "WHERE tenant_id = :t AND version_id = ANY(CAST(:ids AS uuid[])) ORDER BY version_id, tier_number"
        ),
        {"t": tenant_id, **_ids(version_ids)},
    ):
        tiers_by.setdefault(t.version_id, []).append(
            Tier(
                tier_number=t.tier_number,
                lower_bound=t.lower_bound,
                rate=t.rate if t.rate is not None else Decimal(0),
            )
        )

    spa: list[SpaVersion] = []
    hashes: dict[str, str] = {}
    rebates: list[RebateEntry] = []
    for r in rows:
        vid = str(r.id)
        hashes[vid] = r.content_hash
        if r.agreement_type == "spa":
            spa.append(
                SpaVersion(
                    version_id=vid,
                    agreement_id=str(r.agreement_id),
                    external_number=r.external_number,
                    manufacturer_id=str(r.manufacturer_id),
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    priority=r.priority,
                    rules=_parsed(r.external_number, parse_spa_rules, r.rules),
                    items=tuple(
                        SpaItem(
                            product_id=str(i.product_id),
                            reference_cost=i.reference_cost,
                            deviated_cost=i.deviated_cost,
                            sell_price=i.sell_price,
                            rate=i.rate,
                            flat_amount=i.flat_amount,
                            max_quantity=i.max_quantity,
                            claim_uom=i.claim_uom,
                        )
                        for i in items_by.get(r.id, [])
                    ),
                    customers=tuple(customers_by.get(r.id, [])),
                )
            )
        else:
            rebates.append(
                RebateEntry(
                    version=RebateVersion(
                        version_id=vid,
                        external_number=r.external_number,
                        manufacturer_id=str(r.manufacturer_id),
                        rules=_parsed(r.external_number, parse_rebate_rules, r.rules),
                        tiers=tuple(tiers_by.get(r.id, [])),
                        product_ids=tuple(str(i.product_id) for i in items_by.get(r.id, [])),
                    ),
                    agreement_id=r.agreement_id,
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    content_hash=r.content_hash,
                )
            )
    return Versions(tuple(spa), hashes, tuple(rebates))


def customer_ancestors(db: Session, tenant_id: UUID) -> dict[str, tuple[str, ...]]:
    """customer id -> (parent, grandparent, ...); only customers that have a parent appear (§8)."""
    parents = {
        r.id: r.parent_customer_id
        for r in db.execute(
            text("SELECT id, parent_customer_id FROM customers WHERE tenant_id = :t"), {"t": tenant_id}
        )
    }
    out: dict[str, tuple[str, ...]] = {}
    for cid, parent in parents.items():
        chain: list[str] = []
        seen = {cid}
        while parent is not None and parent not in seen:  # the masterdata service forbids cycles; stay safe
            chain.append(str(parent))
            seen.add(parent)
            parent = parents.get(parent)
        if chain:
            out[str(cid)] = tuple(chain)
    return out


@dataclass(frozen=True)
class SalesBatch:
    lines: tuple[SalesLine, ...]
    checksum_rows: tuple[tuple[str, str], ...]  # (line id, row_hash) in id order: input for the run checksum


_SALES = (
    "SELECT l.id, l.document_number, l.line_number, l.invoice_date, l.ship_date, l.customer_id, l.product_id, "
    "l.manufacturer_id, p.base_uom, l.quantity, l.uom, l.unit_price, l.unit_cost, l.replacement_cost, "
    "l.spa_reference, l.original_line_id, l.row_hash "
    "FROM sales_document_lines l "
    "JOIN import_batches b ON b.tenant_id = l.tenant_id AND b.id = l.import_batch_id "
    "LEFT JOIN products p ON p.tenant_id = l.tenant_id AND p.id = l.product_id "
    "WHERE l.tenant_id = :t AND l.is_current AND b.status IN ('completed', 'completed_with_errors') AND b.completed_at <= :cutoff "
    "AND l.manufacturer_id IS NOT DISTINCT FROM CAST(:m AS uuid) "
)


def load_sales_lines(
    db: Session,
    tenant_id: UUID,
    manufacturer_id: UUID | None,
    date_from: date,
    date_to: date,
    data_cutoff: datetime,
    ancestors: dict[str, tuple[str, ...]],
    source: frozen.FrozenClaimSource,
    *,
    product_ids: list[UUID] | None = None,
    limit: int | None = None,
) -> SalesBatch:
    """Sales lines of one manufacturer (None = lines without a known manufacturer) dated in the window, with
    return links, returned quantities and prior claims attached (§7)."""
    params = {"t": tenant_id, "m": manufacturer_id, "cutoff": data_cutoff}
    sql = _SALES + "AND l.invoice_date BETWEEN :d1 AND :d2 "
    extra: dict[str, Any] = {"d1": date_from, "d2": date_to}
    if product_ids is not None:  # preview: only the products the draft covers, newest first, bounded
        sql += "AND l.product_id = ANY(CAST(:products AS uuid[])) "
        extra["products"] = product_ids
    if limit is not None:
        sql += "ORDER BY l.invoice_date DESC, l.id DESC LIMIT :limit"
        extra["limit"] = limit
    else:
        sql += "ORDER BY l.id"
    rows = list(db.execute(text(sql), {**params, **extra}))
    returned: dict[UUID, Decimal] = {
        r.original_line_id: r.returned
        for r in db.execute(
            text(
                "SELECT l.original_line_id, sum(-l.quantity) AS returned "
                "FROM sales_document_lines l "
                "JOIN import_batches b ON b.tenant_id = l.tenant_id AND b.id = l.import_batch_id "
                "WHERE l.tenant_id = :t AND l.is_current AND l.quantity < 0 AND l.original_line_id IS NOT NULL "
                "AND b.status IN ('completed', 'completed_with_errors') AND b.completed_at <= :cutoff AND l.invoice_date <= :cutoff_date "
                "AND l.manufacturer_id IS NOT DISTINCT FROM CAST(:m AS uuid) GROUP BY l.original_line_id"
            ),
            {**params, "cutoff_date": data_cutoff.date()},
        )
    }
    originals = {r.id for r in rows if r.quantity >= 0}
    original_ids = sorted(
        {r.original_line_id for r in rows if r.quantity < 0 and r.original_line_id is not None}
    )
    original_qty: dict[UUID, Decimal] = (
        {
            r.id: r.quantity
            for r in db.execute(
                text(
                    "SELECT id, quantity FROM sales_document_lines "
                    "WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"t": tenant_id, "ids": original_ids},
            )
        }
        if original_ids
        else {}
    )
    prior = source.prior_claims(db, tenant_id, sorted(originals | set(original_ids)))
    lines: list[SalesLine] = []
    for r in rows:
        is_return = r.quantity < 0
        ret_orig = None
        if is_return and r.original_line_id is not None and r.original_line_id in original_qty:
            oid = r.original_line_id
            ret_orig = ReturnOriginal(
                line_id=str(oid),
                quantity=original_qty[oid],
                returned_quantity=returned.get(oid, Decimal(0)),
                prior_claim=prior.get(oid),
            )
        lines.append(
            SalesLine(
                line_id=str(r.id),
                document_number=r.document_number,
                line_number=r.line_number,
                invoice_date=r.invoice_date,
                ship_date=r.ship_date,
                customer_id=str(r.customer_id),
                product_id=None if r.product_id is None else str(r.product_id),
                manufacturer_id=None if r.manufacturer_id is None else str(r.manufacturer_id),
                base_uom=r.base_uom,
                quantity=r.quantity,
                uom=r.uom,
                unit_price=r.unit_price,
                customer_ancestor_ids=ancestors.get(str(r.customer_id), ()),
                unit_cost=r.unit_cost,
                replacement_cost=r.replacement_cost,
                spa_reference=r.spa_reference,
                returned_quantity=Decimal(0) if is_return else returned.get(r.id, Decimal(0)),
                prior_claim=None if is_return else prior.get(r.id),
                return_original=ret_orig,
            )
        )
    return SalesBatch(tuple(lines), tuple((str(r.id), r.row_hash) for r in rows))


def sales_volume_lines(
    db: Session,
    tenant_id: UUID,
    manufacturer_id: UUID,
    date_from: date,
    date_to: date,
    data_cutoff: datetime,
    eligible_line_ids: frozenset[str],
) -> tuple[tuple[VolumeLine, ...], tuple[tuple[str, str], ...]]:
    """Signed sales volume (credit notes negative) for a rebate on the sales basis; ``spa_eligible`` comes from the
    SPA results of the same run (§5.4)."""
    rows = list(
        db.execute(
            text(
                _SALES.replace("l.original_line_id, l.row_hash", "l.extended_amount, l.row_hash")
                + "AND l.invoice_date BETWEEN :d1 AND :d2 AND l.product_id IS NOT NULL ORDER BY l.id"
            ),
            {"t": tenant_id, "m": manufacturer_id, "cutoff": data_cutoff, "d1": date_from, "d2": date_to},
        )
    )
    out = tuple(
        VolumeLine(
            line_id=str(r.id),
            line_date=r.invoice_date,
            manufacturer_id=str(manufacturer_id),
            product_id=str(r.product_id),
            amount=r.extended_amount,
            quantity=r.quantity,
            spa_reference=r.spa_reference,
            spa_eligible=str(r.id) in eligible_line_ids,
        )
        for r in rows
    )
    return out, tuple((str(r.id), r.row_hash) for r in rows)


def purchase_volume_lines(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, date_from: date, date_to: date, data_cutoff: datetime
) -> tuple[tuple[VolumeLine, ...], tuple[tuple[str, str], ...]]:
    """Signed purchase volume (vendor credits negative, normalised at import)."""
    rows = list(
        db.execute(
            text(
                "SELECT l.id, l.document_date, l.product_id, l.quantity, l.extended_amount, l.row_hash "
                "FROM purchase_document_lines l "
                "JOIN import_batches b ON b.tenant_id = l.tenant_id AND b.id = l.import_batch_id "
                "WHERE l.tenant_id = :t AND l.is_current AND b.status IN ('completed', 'completed_with_errors') AND b.completed_at <= :cutoff "
                "AND l.manufacturer_id = :m AND l.document_date BETWEEN :d1 AND :d2 "
                "AND l.product_id IS NOT NULL ORDER BY l.id"
            ),
            {"t": tenant_id, "m": manufacturer_id, "cutoff": data_cutoff, "d1": date_from, "d2": date_to},
        )
    )
    out = tuple(
        VolumeLine(
            line_id=str(r.id),
            line_date=r.document_date,
            manufacturer_id=str(manufacturer_id),
            product_id=str(r.product_id),
            amount=r.extended_amount,
            quantity=r.quantity,
        )
        for r in rows
    )
    return out, tuple((str(r.id), r.row_hash) for r in rows)


def prior_accrued(db: Session, tenant_id: UUID, agreement_id: UUID, period_start: date) -> Decimal:
    """Sum of the deltas of the ledger for this agreement and period, completed runs only (§5.3)."""
    total = db.scalar(
        text(
            "SELECT COALESCE(sum(e.delta), 0) FROM rebate_accrual_entries e "
            "JOIN calculation_runs r ON r.tenant_id = e.tenant_id AND r.id = e.run_id "
            "WHERE e.tenant_id = :t AND e.agreement_id = :a AND e.period_start = :p AND r.status = 'completed'"
        ),
        {"t": tenant_id, "a": agreement_id, "p": period_start},
    )
    return Decimal(total)


def chunks[T](items: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
