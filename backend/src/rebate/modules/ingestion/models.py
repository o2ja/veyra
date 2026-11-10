"""Import pipeline and source transaction tables (RLS; DATABASE_SCHEMA.md §3.4-§3.5). Mirror of the
migrations for ``alembic check``."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    Boolean,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)


def _tenant_fk(table: str) -> ForeignKey:
    return ForeignKey("tenants.id", name=f"fk_{table}_tenants")


def _fk(table: str, column: str, target: str, name: str, **kw: Any) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=f"fk_{table}_{name}", **kw
    )


class MappingTemplate(Base):
    __tablename__ = "mapping_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_mapping_templates_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_mapping_templates_tenant_id_name"),
        Index(
            "ix_mapping_templates_tenant_id_import_type_header_signature",
            "tenant_id",
            "import_type",
            "header_signature",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("mapping_templates"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    import_type: Mapped[str] = mapped_column(Text)
    header_signature: Mapped[str] = mapped_column(CHAR(64))
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_mapping_templates_created_by"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_mapping_templates_updated_by")
    )
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_import_batches_tenant_id_id"),
        _fk("import_batches", "file_id", "file_objects", "file_objects"),
        _fk(
            "import_batches",
            "mapping_template_id",
            "mapping_templates",
            "mapping_templates",
            ondelete="SET NULL (mapping_template_id)",
        ),
        Index("ix_import_batches_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_import_batches_tenant_id_file_id", "tenant_id", "file_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("import_batches"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    import_type: Mapped[str] = mapped_column(Text)
    file_id: Mapped[UUID] = mapped_column()
    mapping_template_id: Mapped[UUID | None] = mapped_column()
    mapping_template_version: Mapped[int | None] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    accept_partial: Mapped[bool] = mapped_column(Boolean, server_default="false")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error_summary: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    detected_encoding: Mapped[str | None] = mapped_column(Text)
    delimiter: Mapped[str | None] = mapped_column(Text)
    sheet_name: Mapped[str | None] = mapped_column(Text)
    header_row: Mapped[int | None] = mapped_column(Integer)
    headers: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    header_signature: Mapped[str | None] = mapped_column(CHAR(64))
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    mapping_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_import_batches_created_by"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    started_at: Mapped[datetime | None] = mapped_column(TS)
    confirmed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_import_batches_confirmed_by")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class ImportStagedRow(Base):
    __tablename__ = "import_staged_rows"
    __table_args__ = (_fk("import_staged_rows", "import_batch_id", "import_batches", "import_batches"),)

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("import_staged_rows"))
    import_batch_id: Mapped[UUID] = mapped_column(primary_key=True)
    row_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    natural_key: Mapped[str | None] = mapped_column(Text)
    row_hash: Mapped[str | None] = mapped_column(CHAR(64))
    phase: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    status: Mapped[str] = mapped_column(Text, server_default="pending")


class ImportRowError(Base):
    __tablename__ = "import_row_errors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_import_row_errors_tenant_id_id"),
        _fk("import_row_errors", "import_batch_id", "import_batches", "import_batches"),
        Index("ix_import_row_errors_tenant_id_import_batch_id", "tenant_id", "import_batch_id", "row_number"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("import_row_errors"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    import_batch_id: Mapped[UUID] = mapped_column()
    row_number: Mapped[int | None] = mapped_column(Integer)
    column_name: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, server_default="error")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class SalesDocument(Base):
    __tablename__ = "sales_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_documents_tenant_id_id"),
        _fk("sales_documents", "import_batch_id", "import_batches", "import_batches"),
        _fk("sales_documents", "customer_id", "customers", "customers"),
        _fk("sales_documents", "branch_id", "branches", "branches"),
        _fk("sales_documents", "original_document_id", "sales_documents", "original"),
        _fk(
            "sales_documents",
            "superseded_by_id",
            "sales_documents",
            "superseded_by",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_sales_documents_natural_key",
            "tenant_id",
            "source_system",
            "doc_type",
            "document_number",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("sales_documents"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    import_batch_id: Mapped[UUID] = mapped_column()
    source_system: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str] = mapped_column(Text)
    document_date: Mapped[date] = mapped_column(Date)
    ship_date: Mapped[date | None] = mapped_column(Date)
    customer_id: Mapped[UUID] = mapped_column()
    branch_id: Mapped[UUID | None] = mapped_column()
    original_document_id: Mapped[UUID | None] = mapped_column()
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    row_hash: Mapped[str] = mapped_column(CHAR(64))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="true")
    superseded_by_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class SalesDocumentLine(Base):
    __tablename__ = "sales_document_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_document_lines_tenant_id_id"),
        _fk("sales_document_lines", "document_id", "sales_documents", "sales_documents"),
        _fk("sales_document_lines", "import_batch_id", "import_batches", "import_batches"),
        _fk("sales_document_lines", "product_id", "products", "products"),
        _fk("sales_document_lines", "manufacturer_id", "manufacturers", "manufacturers"),
        _fk("sales_document_lines", "customer_id", "customers", "customers"),
        _fk("sales_document_lines", "original_line_id", "sales_document_lines", "original"),
        _fk(
            "sales_document_lines",
            "superseded_by_id",
            "sales_document_lines",
            "superseded_by",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_sales_document_lines_natural_key",
            "tenant_id",
            "source_system",
            "document_number",
            "line_number",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index(
            "ix_sales_document_lines_manufacturer_date",
            "tenant_id",
            "manufacturer_id",
            "invoice_date",
            postgresql_where=text("is_current"),
        ),
        Index("ix_sales_document_lines_customer_date", "tenant_id", "customer_id", "invoice_date"),
        Index("ix_sales_document_lines_tenant_id_product_id", "tenant_id", "product_id"),
        Index(
            "ix_sales_document_lines_spa_reference",
            "tenant_id",
            "spa_reference",
            postgresql_where=text("spa_reference IS NOT NULL"),
        ),
        Index("ix_sales_document_lines_tenant_id_original_line_id", "tenant_id", "original_line_id"),
        Index("ix_sales_document_lines_tenant_id_import_batch_id", "tenant_id", "import_batch_id"),
        Index("ix_sales_document_lines_tenant_id_document_id", "tenant_id", "document_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("sales_document_lines"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column()
    import_batch_id: Mapped[UUID] = mapped_column()
    source_row_number: Mapped[int] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str] = mapped_column(Text)
    line_number: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[UUID | None] = mapped_column()
    sku_raw: Mapped[str] = mapped_column(Text)
    manufacturer_id: Mapped[UUID | None] = mapped_column()
    customer_id: Mapped[UUID] = mapped_column()
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    uom: Mapped[str] = mapped_column(Text)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    replacement_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    extended_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    invoice_date: Mapped[date] = mapped_column(Date)
    ship_date: Mapped[date | None] = mapped_column(Date)
    spa_reference: Mapped[str | None] = mapped_column(Text)
    project_reference: Mapped[str | None] = mapped_column(Text)
    original_line_id: Mapped[UUID | None] = mapped_column()
    row_hash: Mapped[str] = mapped_column(CHAR(64))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="true")
    superseded_by_id: Mapped[UUID | None] = mapped_column()
    source_changed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class PurchaseDocument(Base):
    __tablename__ = "purchase_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_purchase_documents_tenant_id_id"),
        _fk("purchase_documents", "import_batch_id", "import_batches", "import_batches"),
        _fk("purchase_documents", "manufacturer_id", "manufacturers", "manufacturers"),
        _fk(
            "purchase_documents",
            "superseded_by_id",
            "purchase_documents",
            "superseded_by",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_purchase_documents_natural_key",
            "tenant_id",
            "source_system",
            "doc_type",
            "document_number",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("purchase_documents"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    import_batch_id: Mapped[UUID] = mapped_column()
    source_system: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str] = mapped_column(Text)
    document_date: Mapped[date] = mapped_column(Date)
    manufacturer_id: Mapped[UUID] = mapped_column()
    po_number: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    row_hash: Mapped[str] = mapped_column(CHAR(64))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="true")
    superseded_by_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class PurchaseDocumentLine(Base):
    __tablename__ = "purchase_document_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_purchase_document_lines_tenant_id_id"),
        _fk("purchase_document_lines", "document_id", "purchase_documents", "purchase_documents"),
        _fk("purchase_document_lines", "import_batch_id", "import_batches", "import_batches"),
        _fk("purchase_document_lines", "product_id", "products", "products"),
        _fk("purchase_document_lines", "manufacturer_id", "manufacturers", "manufacturers"),
        _fk(
            "purchase_document_lines",
            "superseded_by_id",
            "purchase_document_lines",
            "superseded_by",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_purchase_document_lines_natural_key",
            "tenant_id",
            "source_system",
            "document_number",
            "line_number",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index(
            "ix_purchase_document_lines_manufacturer_date",
            "tenant_id",
            "manufacturer_id",
            "document_date",
            postgresql_where=text("is_current"),
        ),
        Index("ix_purchase_document_lines_tenant_id_import_batch_id", "tenant_id", "import_batch_id"),
        Index("ix_purchase_document_lines_tenant_id_document_id", "tenant_id", "document_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("purchase_document_lines"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column()
    import_batch_id: Mapped[UUID] = mapped_column()
    source_row_number: Mapped[int] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str] = mapped_column(Text)
    line_number: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[UUID | None] = mapped_column()
    sku_raw: Mapped[str] = mapped_column(Text)
    manufacturer_id: Mapped[UUID] = mapped_column()
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    uom: Mapped[str] = mapped_column(Text)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    extended_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    document_date: Mapped[date] = mapped_column(Date)
    row_hash: Mapped[str] = mapped_column(CHAR(64))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="true")
    superseded_by_id: Mapped[UUID | None] = mapped_column()
    source_changed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
