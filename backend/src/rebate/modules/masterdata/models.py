"""Master-data tables (RLS; DATABASE_SCHEMA.md §3.3). Mirror of the migrations for ``alembic check``."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)


def _tenant_fk(table: str) -> ForeignKey:
    return ForeignKey("tenants.id", name=f"fk_{table}_tenants")


class Manufacturer(Base):
    __tablename__ = "manufacturers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_manufacturers_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_manufacturers_tenant_id_code"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("manufacturers"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ManufacturerSetting(Base):
    __tablename__ = "manufacturer_settings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "manufacturer_id"],
            ["manufacturers.tenant_id", "manufacturers.id"],
            name="fk_manufacturer_settings_manufacturers",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("manufacturer_settings"))
    manufacturer_id: Mapped[UUID] = mapped_column(primary_key=True)
    claim_window_days: Mapped[int | None] = mapped_column(Integer)
    claim_window_basis: Mapped[str | None] = mapped_column(Text)
    dispute_window_days: Mapped[int | None] = mapped_column(Integer)
    dispute_window_basis: Mapped[str | None] = mapped_column(Text)
    allow_negative_claims: Mapped[bool] = mapped_column(Boolean, server_default="false")
    conflict_policy: Mapped[str] = mapped_column(Text, server_default="flag")
    variance_tolerance: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0.01")
    payment_lag_days_expected: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ReasonCode(Base):
    """Global reference list (no RLS), seeded by the migration; read-only for the app role."""

    __tablename__ = "reason_codes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class ReasonCodeMapping(Base):
    __tablename__ = "reason_code_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_reason_code_mappings_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "manufacturer_id",
            "external_code",
            name="uq_reason_code_mappings_tenant_id_manufacturer_id_external_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "manufacturer_id"],
            ["manufacturers.tenant_id", "manufacturers.id"],
            name="fk_reason_code_mappings_manufacturers",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("reason_code_mappings"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    external_code: Mapped[str] = mapped_column(Text)
    external_text: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(
        ForeignKey("reason_codes.code", name="fk_reason_code_mappings_reason_codes")
    )
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customers_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_customer_id"],
            ["customers.tenant_id", "customers.id"],
            name="fk_customers_parent",
        ),
        Index(
            "uq_customers_tenant_id_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL"),
        ),
        Index(
            "ix_customers_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("customers"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    customer_type: Mapped[str] = mapped_column(Text, server_default="other")
    parent_customer_id: Mapped[UUID | None] = mapped_column()
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class CustomerAlias(Base):
    __tablename__ = "customer_aliases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customer_aliases_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            name="fk_customer_aliases_customers",
        ),
        Index("uq_customer_aliases_tenant_id_alias", "tenant_id", "alias", unique=True),
        Index("ix_customer_aliases_tenant_id_customer_id", "tenant_id", "customer_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("customer_aliases"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    customer_id: Mapped[UUID] = mapped_column()
    alias: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_branches_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_branches_tenant_id_code"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("branches"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_products_tenant_id_id"),
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_id_sku"),
        ForeignKeyConstraint(
            ["tenant_id", "manufacturer_id"],
            ["manufacturers.tenant_id", "manufacturers.id"],
            name="fk_products_manufacturers",
        ),
        Index("ix_products_tenant_id_manufacturer_id", "tenant_id", "manufacturer_id"),
        Index("ix_products_sku_trgm", "sku", postgresql_using="gin", postgresql_ops={"sku": "gin_trgm_ops"}),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("products"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    manufacturer_id: Mapped[UUID] = mapped_column()
    manufacturer_part_number: Mapped[str | None] = mapped_column(Text)
    base_uom: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    list_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ProductAlias(Base):
    __tablename__ = "product_aliases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_product_aliases_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "alias_type", "alias", name="uq_product_aliases_tenant_id_alias_type_alias"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_aliases_products",
        ),
        Index("ix_product_aliases_tenant_id_product_id", "tenant_id", "product_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("product_aliases"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    product_id: Mapped[UUID] = mapped_column()
    alias: Mapped[str] = mapped_column(Text)
    alias_type: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class UomConversion(Base):
    __tablename__ = "uom_conversions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_uom_conversions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "from_uom",
            "to_uom",
            name="uq_uom_conversions_tenant_id_product_id_from_uom_to_uom",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_uom_conversions_products",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("uom_conversions"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    product_id: Mapped[UUID | None] = mapped_column()
    from_uom: Mapped[str] = mapped_column(Text)
    to_uom: Mapped[str] = mapped_column(Text)
    factor: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class UnmappedEntity(Base):
    __tablename__ = "unmapped_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_unmapped_entities_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "external_value",
            name="uq_unmapped_entities_tenant_id_entity_type_external_value",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "first_import_batch_id"],
            ["import_batches.tenant_id", "import_batches.id"],
            name="fk_unmapped_entities_import_batches",
            use_alter=True,
        ),
        Index("ix_unmapped_entities_tenant_id_status", "tenant_id", "status", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("unmapped_entities"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    external_value: Mapped[str] = mapped_column(Text)
    example_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    seen_count: Mapped[int] = mapped_column(Integer, server_default="1")
    first_import_batch_id: Mapped[UUID | None] = mapped_column()
    last_seen_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    resolved_entity_id: Mapped[UUID | None] = mapped_column()
    resolved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_unmapped_entities_users")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
