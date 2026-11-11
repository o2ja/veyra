"""Agreement tables (RLS; DATABASE_SCHEMA.md §3.6). Mirror of migration 20261112_0900 for ``alembic check``.

CHECKs, triggers and the exclusion constraint ``ex_agreement_versions_no_overlap`` live only in the migration
(autogenerate does not compare them)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import DATERANGE, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)


def _tenant_fk(table: str) -> ForeignKey:
    return ForeignKey("tenants.id", name=f"fk_{table}_tenants")


def _user_fk(name: str) -> ForeignKey:
    return ForeignKey("users.id", name=name)


def _fk(column: str, target: str, name: str, *, use_alter: bool = False) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=name, use_alter=use_alter
    )


class Agreement(Base):
    __tablename__ = "agreements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agreements_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "manufacturer_id",
            "external_number",
            name="uq_agreements_tenant_id_manufacturer_id_external_number",
        ),
        _fk("manufacturer_id", "manufacturers", "fk_agreements_manufacturers"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreements"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    agreement_type: Mapped[str] = mapped_column(Text)
    external_number: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[UUID] = mapped_column(_user_fk("fk_agreements_users"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AgreementDraft(Base):
    __tablename__ = "agreement_drafts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agreement_drafts_tenant_id_id"),
        _fk("agreement_id", "agreements", "fk_agreement_drafts_agreements"),
        _fk("source_file_id", "file_objects", "fk_agreement_drafts_file_objects"),
        _fk("amends_version_id", "agreement_versions", "fk_agreement_drafts_amends_version", use_alter=True),
        _fk(
            "resulting_version_id",
            "agreement_versions",
            "fk_agreement_drafts_resulting_version",
            use_alter=True,
        ),
        Index("ix_agreement_drafts_tenant_id_status", "tenant_id", "status", "id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreement_drafts"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    agreement_id: Mapped[UUID | None] = mapped_column()
    amends_version_id: Mapped[UUID | None] = mapped_column()
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_schema_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_id: Mapped[UUID | None] = mapped_column()  # FK to document_extractions arrives in S6
    source_file_id: Mapped[UUID | None] = mapped_column()
    created_by: Mapped[UUID] = mapped_column(_user_fk("fk_agreement_drafts_created_by"))
    submitted_by: Mapped[UUID | None] = mapped_column(_user_fk("fk_agreement_drafts_submitted_by"))
    submitted_at: Mapped[datetime | None] = mapped_column(TS)
    reviewed_by: Mapped[UUID | None] = mapped_column(_user_fk("fk_agreement_drafts_reviewed_by"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    review_comment: Mapped[str | None] = mapped_column(Text)
    resulting_version_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AgreementDraftRevision(Base):
    __tablename__ = "agreement_draft_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agreement_draft_revisions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "draft_id",
            "revision",
            name="uq_agreement_draft_revisions_tenant_id_draft_id_revision",
        ),
        _fk(
            "draft_id",
            "agreement_drafts",
            "fk_agreement_draft_revisions_agreement_drafts",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreement_draft_revisions"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    draft_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    edited_by: Mapped[UUID] = mapped_column(_user_fk("fk_agreement_draft_revisions_users"))
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AgreementVersion(Base):
    __tablename__ = "agreement_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agreement_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "agreement_id",
            "version_number",
            name="uq_agreement_versions_tenant_id_agreement_id_version_number",
        ),
        _fk("agreement_id", "agreements", "fk_agreement_versions_agreements"),
        _fk("draft_id", "agreement_drafts", "fk_agreement_versions_agreement_drafts"),
        _fk("source_file_id", "file_objects", "fk_agreement_versions_file_objects"),
        _fk(
            "derived_from_version_id",
            "agreement_versions",
            "fk_agreement_versions_derived_from",
        ),
        _fk("supersedes_version_id", "agreement_versions", "fk_agreement_versions_supersedes"),
        _fk(
            "superseded_by_version_id",
            "agreement_versions",
            "fk_agreement_versions_superseded_by",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreement_versions"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    agreement_id: Mapped[UUID] = mapped_column()
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    valid_range: Mapped[Any] = mapped_column(
        DATERANGE, Computed("daterange(valid_from, valid_to, '[]')", persisted=True)
    )
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rules_schema_version: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, server_default="0")
    source: Mapped[str] = mapped_column(Text)
    draft_id: Mapped[UUID | None] = mapped_column()
    source_file_id: Mapped[UUID | None] = mapped_column()
    derived_from_version_id: Mapped[UUID | None] = mapped_column()
    supersedes_version_id: Mapped[UUID | None] = mapped_column()
    superseded_by_version_id: Mapped[UUID | None] = mapped_column()
    content_hash: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(_user_fk("fk_agreement_versions_created_by"))
    approved_by: Mapped[UUID] = mapped_column(_user_fk("fk_agreement_versions_approved_by"))
    activated_at: Mapped[datetime] = mapped_column(TS)
    superseded_at: Mapped[datetime | None] = mapped_column(TS)
    created_txid: Mapped[int] = mapped_column(BigInteger)  # forced by trigger to the inserting transaction
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AgreementVersionItem(Base):
    __tablename__ = "agreement_version_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agreement_version_items_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "product_id",
            "customer_id",
            name="uq_agreement_version_items_version_product_customer",
            postgresql_nulls_not_distinct=True,
        ),
        _fk(
            "version_id",
            "agreement_versions",
            "fk_agreement_version_items_agreement_versions",
        ),
        _fk("product_id", "products", "fk_agreement_version_items_products"),
        _fk("customer_id", "customers", "fk_agreement_version_items_customers"),
        Index("ix_agreement_version_items_tenant_id_version_id", "tenant_id", "version_id", "line_number"),
        Index("ix_agreement_version_items_tenant_id_product_id", "tenant_id", "product_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreement_version_items"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    version_id: Mapped[UUID] = mapped_column()
    line_number: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[UUID] = mapped_column()
    customer_id: Mapped[UUID | None] = mapped_column()
    reference_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    deviated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sell_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    flat_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    claim_uom: Mapped[str | None] = mapped_column(Text)
    max_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))


class AgreementVersionCustomer(Base):
    __tablename__ = "agreement_version_customers"
    __table_args__ = (
        _fk(
            "version_id",
            "agreement_versions",
            "fk_agreement_version_customers_agreement_versions",
        ),
        _fk("customer_id", "customers", "fk_agreement_version_customers_customers"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreement_version_customers"))
    version_id: Mapped[UUID] = mapped_column(primary_key=True)
    customer_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(Text)


class AgreementVersionTier(Base):
    __tablename__ = "agreement_version_tiers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agreement_version_tiers_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "tier_number",
            name="uq_agreement_version_tiers_tenant_id_version_id_tier_number",
        ),
        _fk(
            "version_id",
            "agreement_versions",
            "fk_agreement_version_tiers_agreement_versions",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("agreement_version_tiers"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    version_id: Mapped[UUID] = mapped_column()
    tier_number: Mapped[int] = mapped_column(Integer)
    lower_bound: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    flat_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
