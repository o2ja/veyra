"""Master-data services (FR-IM-01, FR-IM-09; API_SPECIFICATION.md §2.3).

Every mutation writes an audit entry in the same transaction. Lists are tenant-scoped by RLS **and** by
an explicit ``tenant_id`` filter. Codes, SKUs and aliases are stored canonical (trimmed, upper case),
the same form the import normaliser produces, so imports match master data exactly.

Bulk loads from imports (customers/products/UoM files) go through ``load_*``: one audit entry per import
batch (``import.completed`` with counts) rather than one per row.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, InvalidState, NotFound, PreconditionFailed, ValidationFailed
from rebate.core.ids import new_id
from rebate.modules import audit
from rebate.modules.audit import Action, Actor
from rebate.modules.masterdata.models import (
    Branch,
    Customer,
    CustomerAlias,
    Manufacturer,
    ManufacturerSetting,
    Product,
    ProductAlias,
    ReasonCode,
    ReasonCodeMapping,
    UnmappedEntity,
    UomConversion,
)
from rebate.modules.masterdata.pagination import keyset


class DuplicateCode(Conflict):
    code = "conflict"
    message = "A record with this code already exists."


@dataclass(frozen=True)
class Who:
    tenant_id: UUID
    user_id: UUID
    meta: dict[str, Any]


def _plain(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """Decimals as strings (money is never a JSON number)."""
    if values is None:
        return None
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in values.items()}


def _audit(db: Session, who: Who, action: Action, entity: str, entity_id: UUID, **kw: Any) -> None:
    for key in ("before", "after"):
        if key in kw:
            kw[key] = _plain(kw[key])
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=Actor("user", who.user_id),
        action=action,
        entity_type=entity,
        entity_id=entity_id,
        meta=who.meta,
        **kw,
    )


def _one[T](db: Session, model: type[T], tenant_id: UUID, row_id: UUID, *, lock: bool = False) -> T:
    stmt: Select[Any] = select(model).where(
        model.tenant_id == tenant_id,  # type: ignore[attr-defined]
        model.id == row_id,  # type: ignore[attr-defined]
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row  # type: ignore[no-any-return]


def _flush_unique(db: Session) -> None:
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if "unique" in str(exc.orig).lower() or "duplicate key" in str(exc.orig).lower():
            raise DuplicateCode() from None
        raise


def _fields(row: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {k: getattr(row, k) for k in names}


def _apply(row: Any, changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = {k: getattr(row, k) for k in changes}
    for k, v in changes.items():
        setattr(row, k, v)
    if hasattr(row, "updated_at"):
        row.updated_at = clock.now()
    return before, changes


# ---- manufacturers ------------------------------------------------------------------------------

MANUFACTURER_FIELDS = ("name", "code", "external_id", "active")


def list_manufacturers(
    db: Session, tenant_id: UUID, *, active: bool | None, limit: int, cursor: str | None
) -> list[Manufacturer]:
    stmt = select(Manufacturer).where(Manufacturer.tenant_id == tenant_id)
    if active is not None:
        stmt = stmt.where(Manufacturer.active == active)
    return list(db.scalars(keyset(stmt, Manufacturer.id, cursor, limit)))


def get_manufacturer(db: Session, tenant_id: UUID, manufacturer_id: UUID) -> Manufacturer:
    return _one(db, Manufacturer, tenant_id, manufacturer_id)


def create_manufacturer(db: Session, who: Who, values: dict[str, Any]) -> Manufacturer:
    now = clock.now()
    row = Manufacturer(tenant_id=who.tenant_id, id=new_id(), created_at=now, updated_at=now, **values)
    db.add(row)
    _flush_unique(db)
    db.add(ManufacturerSetting(tenant_id=who.tenant_id, manufacturer_id=row.id, updated_at=now))
    db.flush()
    _audit(
        db, who, Action.MANUFACTURER_CREATED, "manufacturer", row.id, after=_fields(row, MANUFACTURER_FIELDS)
    )
    return row


def update_manufacturer(
    db: Session, who: Who, manufacturer_id: UUID, changes: dict[str, Any]
) -> Manufacturer:
    row = _one(db, Manufacturer, who.tenant_id, manufacturer_id, lock=True)
    before, after = _apply(row, changes)
    _flush_unique(db)
    _audit(db, who, Action.MANUFACTURER_UPDATED, "manufacturer", row.id, before=before, after=after)
    return row


SETTINGS_FIELDS = (
    "claim_window_days",
    "claim_window_basis",
    "dispute_window_days",
    "dispute_window_basis",
    "allow_negative_claims",
    "conflict_policy",
    "variance_tolerance",
    "payment_lag_days_expected",
    "notes",
)


def get_manufacturer_settings(
    db: Session, tenant_id: UUID, manufacturer_id: UUID, *, lock: bool = False
) -> ManufacturerSetting:
    stmt = select(ManufacturerSetting).where(
        ManufacturerSetting.tenant_id == tenant_id, ManufacturerSetting.manufacturer_id == manufacturer_id
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def put_manufacturer_settings(
    db: Session, who: Who, manufacturer_id: UUID, expected_revision: int, values: dict[str, Any]
) -> ManufacturerSetting:
    row = get_manufacturer_settings(db, who.tenant_id, manufacturer_id, lock=True)
    if row.revision != expected_revision:
        raise PreconditionFailed()
    before, after = _apply(row, values)
    row.revision += 1
    db.flush()
    _audit(
        db,
        who,
        Action.MANUFACTURER_SETTINGS_UPDATED,
        "manufacturer_settings",
        manufacturer_id,
        before=before,
        after=after,
    )
    db.refresh(row)  # numeric scale as stored (e.g. factor 12 -> 12.00000000)
    return row


def list_reason_code_mappings(db: Session, tenant_id: UUID, manufacturer_id: UUID) -> list[ReasonCodeMapping]:
    get_manufacturer(db, tenant_id, manufacturer_id)
    return list(
        db.scalars(
            select(ReasonCodeMapping)
            .where(
                ReasonCodeMapping.tenant_id == tenant_id, ReasonCodeMapping.manufacturer_id == manufacturer_id
            )
            .order_by(ReasonCodeMapping.external_code)
        )
    )


def replace_reason_code_mappings(
    db: Session, who: Who, manufacturer_id: UUID, mappings: list[dict[str, Any]]
) -> list[ReasonCodeMapping]:
    """Replace the manufacturer's whole mapping table (PUT semantics)."""
    _one(db, Manufacturer, who.tenant_id, manufacturer_id, lock=True)
    known = set(db.scalars(select(ReasonCode.code)))
    codes = [m["external_code"] for m in mappings]
    details = [
        {
            "field": f"mappings.{i}.reason_code",
            "code": "unknown_reason_code",
            "message": "Unknown reason code.",
        }
        for i, m in enumerate(mappings)
        if m["reason_code"] not in known
    ] + [
        {"field": f"mappings.{i}.external_code", "code": "duplicate", "message": "Listed twice."}
        for i, c in enumerate(codes)
        if c in codes[:i]
    ]
    if details:
        raise ValidationFailed(details=details)
    old = list_reason_code_mappings(db, who.tenant_id, manufacturer_id)
    for row in old:
        db.delete(row)
    db.flush()
    rows = [
        ReasonCodeMapping(tenant_id=who.tenant_id, id=new_id(), manufacturer_id=manufacturer_id, **m)
        for m in mappings
    ]
    db.add_all(rows)
    db.flush()
    _audit(
        db,
        who,
        Action.REASON_CODE_MAPPINGS_REPLACED,
        "manufacturer",
        manufacturer_id,
        metadata={
            "before": {r.external_code: r.reason_code for r in old},
            "after": {r.external_code: r.reason_code for r in rows},
        },
    )
    return sorted(rows, key=lambda r: r.external_code)


# ---- customers ----------------------------------------------------------------------------------

CUSTOMER_FIELDS = ("name", "code", "external_id", "customer_type", "parent_customer_id", "active")


def list_customers(
    db: Session, tenant_id: UUID, *, q: str | None, active: bool | None, limit: int, cursor: str | None
) -> list[Customer]:
    stmt = select(Customer).where(Customer.tenant_id == tenant_id)
    if q:
        pattern = f"%{_like(q)}%"
        stmt = stmt.where(or_(Customer.name.ilike(pattern), Customer.code.ilike(pattern)))
    if active is not None:
        stmt = stmt.where(Customer.active == active)
    return list(db.scalars(keyset(stmt, Customer.id, cursor, limit)))


def _like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_customer(db: Session, tenant_id: UUID, customer_id: UUID) -> Customer:
    return _one(db, Customer, tenant_id, customer_id)


def customer_aliases(db: Session, tenant_id: UUID, customer_id: UUID) -> list[CustomerAlias]:
    return list(
        db.scalars(
            select(CustomerAlias)
            .where(CustomerAlias.tenant_id == tenant_id, CustomerAlias.customer_id == customer_id)
            .order_by(CustomerAlias.alias)
        )
    )


def _check_parent(db: Session, tenant_id: UUID, customer_id: UUID | None, parent_id: UUID | None) -> None:
    """The parent exists in this workspace and the hierarchy stays acyclic."""
    seen = {customer_id}
    current = parent_id
    while current is not None:
        if current in seen:
            raise ValidationFailed(
                details=[
                    {"field": "parent_customer_id", "code": "cycle", "message": "This would create a cycle."}
                ]
            )
        seen.add(current)
        parent = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.id == current))
        if parent is None:
            raise ValidationFailed(
                details=[{"field": "parent_customer_id", "code": "not_found", "message": "Unknown customer."}]
            )
        current = parent.parent_customer_id


def create_customer(db: Session, who: Who, values: dict[str, Any]) -> Customer:
    _check_parent(db, who.tenant_id, None, values.get("parent_customer_id"))
    now = clock.now()
    row = Customer(tenant_id=who.tenant_id, id=new_id(), created_at=now, updated_at=now, **values)
    db.add(row)
    _flush_unique(db)
    _audit(db, who, Action.CUSTOMER_CREATED, "customer", row.id, after=_fields(row, CUSTOMER_FIELDS))
    _auto_resolve(db, who, "customer", row.code, row.id)
    return row


def update_customer(db: Session, who: Who, customer_id: UUID, changes: dict[str, Any]) -> Customer:
    row = _one(db, Customer, who.tenant_id, customer_id, lock=True)
    if "parent_customer_id" in changes:
        _check_parent(db, who.tenant_id, row.id, changes["parent_customer_id"])
    before, after = _apply(row, changes)
    _flush_unique(db)
    _audit(db, who, Action.CUSTOMER_UPDATED, "customer", row.id, before=before, after=after)
    return row


def add_customer_alias(
    db: Session, who: Who, customer_id: UUID, alias: str, source: str | None
) -> CustomerAlias:
    _one(db, Customer, who.tenant_id, customer_id)
    row = CustomerAlias(
        tenant_id=who.tenant_id, id=new_id(), customer_id=customer_id, alias=alias, source=source
    )
    db.add(row)
    _flush_unique(db)
    _audit(db, who, Action.CUSTOMER_ALIAS_ADDED, "customer", customer_id, after={"alias": alias})
    _auto_resolve(db, who, "customer", alias, customer_id)
    return row


# ---- branches -----------------------------------------------------------------------------------


def list_branches(db: Session, tenant_id: UUID, *, limit: int, cursor: str | None) -> list[Branch]:
    return list(
        db.scalars(keyset(select(Branch).where(Branch.tenant_id == tenant_id), Branch.id, cursor, limit))
    )


def create_branch(db: Session, who: Who, values: dict[str, Any]) -> Branch:
    row = Branch(tenant_id=who.tenant_id, id=new_id(), **values)
    db.add(row)
    _flush_unique(db)
    _audit(db, who, Action.BRANCH_CREATED, "branch", row.id, after=_fields(row, ("name", "code")))
    return row


# ---- products -----------------------------------------------------------------------------------

PRODUCT_FIELDS = (
    "sku",
    "description",
    "manufacturer_id",
    "manufacturer_part_number",
    "base_uom",
    "category",
    "list_cost",
    "active",
)


def list_products(
    db: Session,
    tenant_id: UUID,
    *,
    q: str | None,
    manufacturer_id: UUID | None,
    active: bool | None,
    limit: int,
    cursor: str | None,
) -> list[Product]:
    stmt = select(Product).where(Product.tenant_id == tenant_id)
    if q:
        pattern = f"%{_like(q)}%"
        stmt = stmt.where(or_(Product.sku.ilike(pattern), Product.description.ilike(pattern)))
    if manufacturer_id is not None:
        stmt = stmt.where(Product.manufacturer_id == manufacturer_id)
    if active is not None:
        stmt = stmt.where(Product.active == active)
    return list(db.scalars(keyset(stmt, Product.id, cursor, limit)))


def get_product(db: Session, tenant_id: UUID, product_id: UUID) -> Product:
    return _one(db, Product, tenant_id, product_id)


def product_aliases(db: Session, tenant_id: UUID, product_id: UUID) -> list[ProductAlias]:
    return list(
        db.scalars(
            select(ProductAlias)
            .where(ProductAlias.tenant_id == tenant_id, ProductAlias.product_id == product_id)
            .order_by(ProductAlias.alias_type, ProductAlias.alias)
        )
    )


def _check_manufacturer(db: Session, tenant_id: UUID, manufacturer_id: UUID) -> None:
    if (
        db.scalar(
            select(Manufacturer.id).where(
                Manufacturer.tenant_id == tenant_id, Manufacturer.id == manufacturer_id
            )
        )
        is None
    ):
        raise ValidationFailed(
            details=[{"field": "manufacturer_id", "code": "not_found", "message": "Unknown manufacturer."}]
        )


def create_product(db: Session, who: Who, values: dict[str, Any]) -> Product:
    _check_manufacturer(db, who.tenant_id, values["manufacturer_id"])
    now = clock.now()
    row = Product(tenant_id=who.tenant_id, id=new_id(), created_at=now, updated_at=now, **values)
    db.add(row)
    _flush_unique(db)
    _audit(db, who, Action.PRODUCT_CREATED, "product", row.id, after=_fields(row, PRODUCT_FIELDS))
    _auto_resolve(db, who, "product", row.sku, row.id)
    db.refresh(row)  # numeric scale as stored (e.g. factor 12 -> 12.00000000)
    return row


def update_product(db: Session, who: Who, product_id: UUID, changes: dict[str, Any]) -> Product:
    row = _one(db, Product, who.tenant_id, product_id, lock=True)
    if "manufacturer_id" in changes:
        _check_manufacturer(db, who.tenant_id, changes["manufacturer_id"])
    before, after = _apply(row, changes)
    _flush_unique(db)
    _audit(db, who, Action.PRODUCT_UPDATED, "product", row.id, before=before, after=after)
    db.refresh(row)  # numeric scale as stored (e.g. factor 12 -> 12.00000000)
    return row


def add_product_alias(db: Session, who: Who, product_id: UUID, alias: str, alias_type: str) -> ProductAlias:
    _one(db, Product, who.tenant_id, product_id)
    row = ProductAlias(
        tenant_id=who.tenant_id, id=new_id(), product_id=product_id, alias=alias, alias_type=alias_type
    )
    db.add(row)
    _flush_unique(db)
    _audit(
        db,
        who,
        Action.PRODUCT_ALIAS_ADDED,
        "product",
        product_id,
        after={"alias": alias, "alias_type": alias_type},
    )
    _auto_resolve(db, who, "product", alias, product_id)
    return row


# ---- UoM conversions ----------------------------------------------------------------------------

UOM_FIELDS = ("product_id", "from_uom", "to_uom", "factor")


def list_uom_conversions(
    db: Session, tenant_id: UUID, *, product_id: UUID | None, limit: int, cursor: str | None
) -> list[UomConversion]:
    stmt = select(UomConversion).where(UomConversion.tenant_id == tenant_id)
    if product_id is not None:
        stmt = stmt.where(UomConversion.product_id == product_id)
    return list(db.scalars(keyset(stmt, UomConversion.id, cursor, limit)))


def _check_product(db: Session, tenant_id: UUID, product_id: UUID | None) -> None:
    if (
        product_id is not None
        and db.scalar(select(Product.id).where(Product.tenant_id == tenant_id, Product.id == product_id))
        is None
    ):
        raise ValidationFailed(
            details=[{"field": "product_id", "code": "not_found", "message": "Unknown product."}]
        )


def create_uom_conversion(db: Session, who: Who, values: dict[str, Any]) -> UomConversion:
    _check_product(db, who.tenant_id, values.get("product_id"))
    now = clock.now()
    row = UomConversion(tenant_id=who.tenant_id, id=new_id(), created_at=now, updated_at=now, **values)
    db.add(row)
    _flush_unique(db)
    _audit(db, who, Action.UOM_CONVERSION_CREATED, "uom_conversion", row.id, after=_fields(row, UOM_FIELDS))
    db.refresh(row)  # numeric scale as stored (e.g. factor 12 -> 12.00000000)
    return row


def update_uom_conversion(db: Session, who: Who, conversion_id: UUID, factor: Decimal) -> UomConversion:
    row = _one(db, UomConversion, who.tenant_id, conversion_id, lock=True)
    before, after = _apply(row, {"factor": factor})
    db.flush()
    _audit(db, who, Action.UOM_CONVERSION_UPDATED, "uom_conversion", row.id, before=before, after=after)
    db.refresh(row)  # numeric scale as stored (e.g. factor 12 -> 12.00000000)
    return row


def delete_uom_conversion(db: Session, who: Who, conversion_id: UUID) -> None:
    row = _one(db, UomConversion, who.tenant_id, conversion_id, lock=True)
    before = _fields(row, UOM_FIELDS)
    db.delete(row)
    db.flush()
    _audit(db, who, Action.UOM_CONVERSION_DELETED, "uom_conversion", conversion_id, before=before)


# ---- unmapped-entity queue ----------------------------------------------------------------------


def list_unmapped(
    db: Session,
    tenant_id: UUID,
    *,
    status: str | None,
    entity_type: str | None,
    limit: int,
    cursor: str | None,
) -> list[UnmappedEntity]:
    stmt = select(UnmappedEntity).where(UnmappedEntity.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(UnmappedEntity.status == status)
    if entity_type is not None:
        stmt = stmt.where(UnmappedEntity.entity_type == entity_type)
    return list(db.scalars(keyset(stmt, UnmappedEntity.id, cursor, limit)))


def record_unmapped(
    db: Session,
    tenant_id: UUID,
    batch_id: UUID,
    seen: dict[tuple[str, str], tuple[int, str | None]],
) -> None:
    """Upsert import sightings ``{(entity_type, value): (count, example name)}``. Resolved or ignored
    entries keep their status; open ones count up."""
    now = clock.now()
    for (kind, value), (count, name) in sorted(seen.items()):
        stmt = insert(UnmappedEntity).values(
            tenant_id=tenant_id,
            id=new_id(),
            entity_type=kind,
            external_value=value,
            example_name=(name or None) and name[:200],
            seen_count=count,
            first_import_batch_id=batch_id,
            last_seen_at=now,
            created_at=now,
        )
        db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_unmapped_entities_tenant_id_entity_type_external_value",
                set_={
                    "seen_count": UnmappedEntity.seen_count + count,
                    "last_seen_at": now,
                    "example_name": func.coalesce(UnmappedEntity.example_name, stmt.excluded.example_name),
                },
            )
        )


def _auto_resolve(db: Session, who: Who, kind: str, value: str | None, entity_id: UUID) -> None:
    """A new master record or alias matching an open queue entry resolves it."""
    if value is None:
        return
    row = db.scalar(
        select(UnmappedEntity)
        .where(
            UnmappedEntity.tenant_id == who.tenant_id,
            UnmappedEntity.entity_type == kind,
            UnmappedEntity.external_value == value,
            UnmappedEntity.status == "open",
        )
        .with_for_update()
    )
    if row is not None:
        _resolve(db, who, row, entity_id)


def _resolve(db: Session, who: Who, row: UnmappedEntity, entity_id: UUID) -> None:
    row.status, row.resolved_entity_id, row.resolved_by, row.resolved_at = (
        "resolved",
        entity_id,
        who.user_id,
        clock.now(),
    )
    db.flush()
    _audit(
        db,
        who,
        Action.UNMAPPED_ENTITY_RESOLVED,
        "unmapped_entity",
        row.id,
        after={"status": "resolved", "resolved_entity_id": entity_id, "external_value": row.external_value},
    )


def resolve_unmapped(
    db: Session, who: Who, entry_id: UUID, action: str, target_id: UUID | None
) -> UnmappedEntity:
    """``map``: add the external value as an alias of an existing customer/product (future imports match);
    ``ignore``: leave rows with this value rejected and stop listing it as open."""
    row = _one(db, UnmappedEntity, who.tenant_id, entry_id, lock=True)
    if row.status != "open":
        raise InvalidState("This entry is already resolved or ignored.")
    if action == "ignore":
        row.status, row.resolved_by, row.resolved_at = "ignored", who.user_id, clock.now()
        db.flush()
        _audit(
            db, who, Action.UNMAPPED_ENTITY_RESOLVED, "unmapped_entity", row.id, after={"status": "ignored"}
        )
        return row
    if target_id is None:
        raise ValidationFailed(
            details=[{"field": "target_id", "code": "required", "message": "Choose the record."}]
        )
    if row.entity_type == "customer":
        _one(db, Customer, who.tenant_id, target_id)
        alias = CustomerAlias(
            tenant_id=who.tenant_id,
            id=new_id(),
            customer_id=target_id,
            alias=row.external_value,
            source="unmapped_queue",
        )
        db.add(alias)
        _flush_unique(db)
        _audit(
            db, who, Action.CUSTOMER_ALIAS_ADDED, "customer", target_id, after={"alias": row.external_value}
        )
    else:
        _one(db, Product, who.tenant_id, target_id)
        p_alias = ProductAlias(
            tenant_id=who.tenant_id,
            id=new_id(),
            product_id=target_id,
            alias=row.external_value,
            alias_type="erp_sku",
        )
        db.add(p_alias)
        _flush_unique(db)
        _audit(
            db,
            who,
            Action.PRODUCT_ALIAS_ADDED,
            "product",
            target_id,
            after={"alias": row.external_value, "alias_type": "erp_sku"},
        )
    _resolve(db, who, row, target_id)
    return row


# ---- lookups and bulk loads for the importer ----------------------------------------------------


@dataclass(frozen=True)
class MasterIndex:
    customers: dict[str, str]
    products: dict[str, tuple[str, str]]
    branches: dict[str, str]
    manufacturers: dict[str, str]


def master_index(db: Session, tenant_id: UUID) -> MasterIndex:
    """Codes/SKUs and aliases -> ids, for the import validator. A master code wins over an alias."""
    customers: dict[str, str] = {}
    for alias, cid in db.execute(
        select(CustomerAlias.alias, CustomerAlias.customer_id).where(CustomerAlias.tenant_id == tenant_id)
    ):
        customers[alias] = str(cid)
    for code, cid in db.execute(
        select(Customer.code, Customer.id).where(Customer.tenant_id == tenant_id, Customer.code.is_not(None))
    ):
        if code is not None:
            customers[code] = str(cid)
    products: dict[str, tuple[str, str]] = {}
    product_rows = {
        pid: (sku, str(pid), str(mid))
        for pid, sku, mid in db.execute(
            select(Product.id, Product.sku, Product.manufacturer_id).where(Product.tenant_id == tenant_id)
        )
    }
    for alias, pid in db.execute(
        select(ProductAlias.alias, ProductAlias.product_id)
        .where(ProductAlias.tenant_id == tenant_id)
        .order_by(ProductAlias.alias_type.desc())  # erp_sku last, so it wins over other alias types
    ):
        _, p, m = product_rows[pid]
        products[alias] = (p, m)
    for sku, p, m in product_rows.values():
        products[sku] = (p, m)
    branches = {
        code: str(bid)
        for code, bid in db.execute(select(Branch.code, Branch.id).where(Branch.tenant_id == tenant_id))
    }
    manufacturers = {
        code: str(mid)
        for code, mid in db.execute(
            select(Manufacturer.code, Manufacturer.id).where(Manufacturer.tenant_id == tenant_id)
        )
    }
    return MasterIndex(customers, products, branches, manufacturers)


@dataclass
class LoadCounts:
    inserted: int = 0
    unchanged: int = 0
    changed: int = 0


def load_customers(db: Session, tenant_id: UUID, rows: list[dict[str, Any]], counts: LoadCounts) -> None:
    """Upsert by code. ``parent_customer_code`` resolves against rows already loaded (phase order)."""
    codes = [r["customer_code"] for r in rows]
    parents = {r["parent_customer_code"] for r in rows if r["parent_customer_code"]}
    existing = {
        c.code: c
        for c in db.scalars(
            select(Customer)
            .where(Customer.tenant_id == tenant_id, Customer.code.in_(codes))
            .with_for_update()
        )
    }
    parent_ids = dict(
        db.execute(
            select(Customer.code, Customer.id).where(
                Customer.tenant_id == tenant_id, Customer.code.in_(parents)
            )
        ).all()
    )
    now = clock.now()
    for r in rows:
        values = {
            "name": r["customer_name"],
            "customer_type": r["customer_type"],
            "external_id": r["external_id"],
            "active": r["active"],
            "parent_customer_id": parent_ids.get(r["parent_customer_code"])
            if r["parent_customer_code"]
            else None,
        }
        if r["parent_customer_code"] and values["parent_customer_id"] is None:
            raise InvalidState(f"parent customer {r['parent_customer_code']} vanished during the import")
        current = existing.get(r["customer_code"])
        if current is None:
            new = Customer(
                tenant_id=tenant_id,
                id=new_id(),
                code=r["customer_code"],
                created_at=now,
                updated_at=now,
                **values,
            )
            db.add(new)
            parent_ids[new.code] = new.id  # a later row of this chunk may name it as parent
            counts.inserted += 1
        elif any(getattr(current, k) != v for k, v in values.items()):
            _apply(current, values)
            counts.changed += 1
        else:
            counts.unchanged += 1
        if current is not None:
            parent_ids[r["customer_code"]] = current.id
    db.flush()


def load_products(db: Session, tenant_id: UUID, rows: list[dict[str, Any]], counts: LoadCounts) -> None:
    skus = [r["sku"] for r in rows]
    existing = {
        p.sku: p
        for p in db.scalars(
            select(Product).where(Product.tenant_id == tenant_id, Product.sku.in_(skus)).with_for_update()
        )
    }
    now = clock.now()
    for r in rows:
        values = {
            "description": r["description"],
            "manufacturer_id": UUID(r["manufacturer_id"]),
            "manufacturer_part_number": r["manufacturer_part_number"],
            "base_uom": r["base_uom"],
            "category": r["category"],
            "list_cost": None if r["list_cost"] is None else Decimal(r["list_cost"]),
            "active": r["active"],
        }
        current = existing.get(r["sku"])
        if current is None:
            db.add(
                Product(
                    tenant_id=tenant_id, id=new_id(), sku=r["sku"], created_at=now, updated_at=now, **values
                )
            )
            counts.inserted += 1
        elif any(getattr(current, k) != v for k, v in values.items()):
            _apply(current, values)
            counts.changed += 1
        else:
            counts.unchanged += 1
    db.flush()


def resolve_matching(db: Session, who: Who, kind: str, values: list[str]) -> None:
    """After a customers/products import: open queue entries whose value now exists are resolved."""
    if not values:
        return
    model, column = (Customer, Customer.code) if kind == "customer" else (Product, Product.sku)
    found = db.execute(
        select(column, model.id).where(model.tenant_id == who.tenant_id, column.in_(values))
    ).all()
    for value, entity_id in sorted(found, key=lambda t: str(t[0])):
        if value is not None:
            _auto_resolve(db, who, kind, value, entity_id)


def load_uom_conversions(
    db: Session, tenant_id: UUID, rows: list[dict[str, Any]], counts: LoadCounts
) -> None:
    now = clock.now()
    for r in rows:
        product_id = UUID(r["product_id"]) if r["product_id"] else None
        current = db.scalar(
            select(UomConversion)
            .where(
                UomConversion.tenant_id == tenant_id,
                UomConversion.product_id.is_(None)
                if product_id is None
                else UomConversion.product_id == product_id,
                UomConversion.from_uom == r["from_uom"],
                UomConversion.to_uom == r["to_uom"],
            )
            .with_for_update()
        )
        factor = Decimal(r["factor"])
        if current is None:
            db.add(
                UomConversion(
                    tenant_id=tenant_id, id=new_id(), product_id=product_id, from_uom=r["from_uom"],
                    to_uom=r["to_uom"], factor=factor, created_at=now, updated_at=now,
                )
            )  # fmt: skip
            counts.inserted += 1
        elif current.factor != factor:
            _apply(current, {"factor": factor})
            counts.changed += 1
        else:
            counts.unchanged += 1
    db.flush()
