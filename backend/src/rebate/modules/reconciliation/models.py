"""Remittance and reconciliation tables (RLS; DATABASE_SCHEMA.md §3.9). Mirror of migrations 20261117_0900,
20261117_0910 and 20261118_0900 for ``alembic check``. CHECKs and triggers live only in the migrations."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
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


def _fk(table: str, column: str, target: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=f"fk_{table}_{target}"
    )


class RemittanceDocument(Base):
    __tablename__ = "remittance_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_remittance_documents_tenant_id_id"),
        _fk("remittance_documents", "manufacturer_id", "manufacturers"),
        _fk("remittance_documents", "file_id", "file_objects"),
        _fk("remittance_documents", "extraction_id", "document_extractions"),
        _fk("remittance_documents", "mapping_template_id", "mapping_templates"),
        Index(
            "uq_remittance_documents_number",
            "tenant_id",
            "manufacturer_id",
            "document_number",
            unique=True,
            postgresql_where=text("status <> 'void' AND document_number IS NOT NULL"),
        ),
        Index("ix_remittance_documents_tenant_id_status_id", "tenant_id", "status", "id"),
        Index("ix_remittance_documents_tenant_id_manufacturer_id", "tenant_id", "manufacturer_id", "id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("remittance_documents"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[UUID] = mapped_column()
    doc_type: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str | None] = mapped_column(Text)
    document_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="uploaded")
    extraction_id: Mapped[UUID | None] = mapped_column()
    mapping_template_id: Mapped[UUID | None] = mapped_column()
    accepted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_remittance_documents_users_accepted")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    finalised_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_remittance_documents_users_finalised")
    )
    finalised_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", name="fk_remittance_documents_users_created")
    )
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class _LineColumns:
    """Columns shared by accepted lines and their staging rows."""

    source_row_number: Mapped[int | None] = mapped_column(Integer)
    claim_reference: Mapped[str | None] = mapped_column(Text)
    invoice_number: Mapped[str | None] = mapped_column(Text)
    invoice_line_number: Mapped[int | None] = mapped_column(Integer)
    sku_raw: Mapped[str | None] = mapped_column(Text)
    customer_ref: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    amount_claimed_stated: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    external_reason_code: Mapped[str | None] = mapped_column(Text)
    external_reason_text: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class RemittanceLine(Base, _LineColumns):
    __tablename__ = "remittance_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_remittance_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "remittance_document_id", "line_number", name="uq_remittance_lines_line_number"
        ),
        _fk("remittance_lines", "remittance_document_id", "remittance_documents"),
        Index("ix_remittance_lines_tenant_id_claim_reference", "tenant_id", "claim_reference"),
        Index("ix_remittance_lines_tenant_id_invoice", "tenant_id", "invoice_number", "invoice_line_number"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("remittance_lines"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    remittance_document_id: Mapped[UUID] = mapped_column()
    line_number: Mapped[int] = mapped_column(Integer)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    reason_code: Mapped[str | None] = mapped_column(
        Text, ForeignKey("reason_codes.code", name="fk_remittance_lines_reason_codes")
    )
    # a person's decision that an unmatched line is an unmatched payment (migration 20261118_0900; BL §10.3)
    category: Mapped[str | None] = mapped_column(Text)
    categorised_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_remittance_lines_users_categorised")
    )
    categorised_at: Mapped[datetime | None] = mapped_column(TS)


class RemittanceStagingLine(Base, _LineColumns):
    __tablename__ = "remittance_extraction_staging"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_remittance_extraction_staging_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "remittance_document_id",
            "line_number",
            name="uq_remittance_extraction_staging_line_number",
        ),
        _fk("remittance_extraction_staging", "remittance_document_id", "remittance_documents"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("remittance_extraction_staging"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    remittance_document_id: Mapped[UUID] = mapped_column()
    line_number: Mapped[int] = mapped_column(Integer)
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    issues: Mapped[list[dict[str, str]]] = mapped_column(JSONB, server_default=text("'[]'"))
    corrected: Mapped[bool] = mapped_column(Boolean, server_default="false")


class ReconciliationMatch(Base):
    __tablename__ = "reconciliation_matches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_reconciliation_matches_tenant_id_id"),
        _fk("reconciliation_matches", "remittance_line_id", "remittance_lines"),
        _fk("reconciliation_matches", "claim_line_id", "claim_lines"),
        _fk("reconciliation_matches", "claim_adjustment_id", "claim_adjustments"),
        Index("ix_reconciliation_matches_tenant_id_claim_line_id", "tenant_id", "claim_line_id"),
        Index("ix_reconciliation_matches_tenant_id_remittance_line_id", "tenant_id", "remittance_line_id"),
        Index("ix_reconciliation_matches_tenant_id_match_id", "tenant_id", "match_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("reconciliation_matches"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    match_id: Mapped[UUID] = mapped_column()
    remittance_line_id: Mapped[UUID] = mapped_column()
    claim_line_id: Mapped[UUID | None] = mapped_column()
    claim_adjustment_id: Mapped[UUID | None] = mapped_column()
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    method: Mapped[str] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    matched_by_run_id: Mapped[UUID | None] = mapped_column()
    confirmed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_reconciliation_matches_users_confirmed")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(TS)
    rejected_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_reconciliation_matches_users_rejected")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
