"""Calculation tables (RLS; DATABASE_SCHEMA.md §3.7-§3.8). Mirror of migration 20261114_0900 for ``alembic check``.
CHECKs and triggers live only in the migration."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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


class CalculationRun(Base):
    __tablename__ = "calculation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_calculation_runs_tenant_id_id"),
        Index(
            "uq_calculation_runs_active_scope",
            "tenant_id",
            "scope_hash",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("calculation_runs"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    scope_hash: Mapped[str] = mapped_column(Text)
    as_of: Mapped[datetime] = mapped_column(TS)
    data_cutoff: Mapped[datetime] = mapped_column(TS)
    run_date: Mapped[date] = mapped_column(Date)
    engine_version: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(Text)
    agreement_version_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default=text("'{}'"))
    input_checksum: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_calculation_runs_users"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    started_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class CalculationResult(Base):
    __tablename__ = "calculation_results"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_calculation_results_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "run_id", "sales_document_line_id", name="uq_calculation_results_run_line"
        ),
        _fk("calculation_results", "run_id", "calculation_runs"),
        _fk("calculation_results", "sales_document_line_id", "sales_document_lines"),
        _fk("calculation_results", "agreement_version_id", "agreement_versions"),
        _fk("calculation_results", "manufacturer_id", "manufacturers"),
        _fk("calculation_results", "agreement_id", "agreements"),
        Index("ix_calculation_results_tenant_id_run_id_status", "tenant_id", "run_id", "status", "id"),
        Index("ix_calculation_results_tenant_id_run_id_id", "tenant_id", "run_id", "id"),
        Index(
            "ix_calculation_results_tenant_id_sales_document_line_id", "tenant_id", "sales_document_line_id"
        ),
        Index("ix_calculation_results_tenant_id_agreement_version_id", "tenant_id", "agreement_version_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("calculation_results"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column()
    sales_document_line_id: Mapped[UUID] = mapped_column()
    document_number: Mapped[str] = mapped_column(Text)
    line_number: Mapped[int] = mapped_column(Integer)
    invoice_date: Mapped[date] = mapped_column(Date)
    manufacturer_id: Mapped[UUID | None] = mapped_column()
    agreement_id: Mapped[UUID | None] = mapped_column()
    agreement_version_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text)
    exception_code: Mapped[str | None] = mapped_column(Text)
    eligible_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    excess_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    claim_uom: Mapped[str | None] = mapped_column(Text)
    claim_unit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    claim_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    trace: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class RebateAccrualEntry(Base):
    __tablename__ = "rebate_accrual_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rebate_accrual_entries_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "agreement_id",
            "period_start",
            name="uq_rebate_accrual_entries_run_agreement_period",
        ),
        _fk("rebate_accrual_entries", "run_id", "calculation_runs"),
        _fk("rebate_accrual_entries", "agreement_id", "agreements"),
        _fk("rebate_accrual_entries", "agreement_version_id", "agreement_versions"),
        _fk("rebate_accrual_entries", "manufacturer_id", "manufacturers"),
        Index(
            "ix_rebate_accrual_entries_tenant_id_agreement_id_period_start",
            "tenant_id",
            "agreement_id",
            "period_start",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("rebate_accrual_entries"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column()
    agreement_id: Mapped[UUID] = mapped_column()
    agreement_version_id: Mapped[UUID] = mapped_column()
    manufacturer_id: Mapped[UUID] = mapped_column()
    external_number: Mapped[str] = mapped_column(Text)
    agreement_name: Mapped[str] = mapped_column(Text)
    tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    as_of_date: Mapped[date] = mapped_column(Date)
    cumulative_qualifying: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    cumulative_payout_base: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    tier_number: Mapped[int | None] = mapped_column(Integer)
    tier_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    cumulative_earned: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    prior_cumulative_accrued: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    claim_deadline: Mapped[date | None] = mapped_column(Date)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    trace: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ClaimAdjustment(Base):
    __tablename__ = "claim_adjustments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_claim_adjustments_tenant_id_id"),
        _fk("claim_adjustments", "manufacturer_id", "manufacturers"),
        _fk("claim_adjustments", "calculation_result_id", "calculation_results"),
        _fk("claim_adjustments", "run_id", "calculation_runs"),
        Index(
            "uq_claim_adjustments_proposal",
            "tenant_id",
            "claim_line_id",
            "adjustment_type",
            "run_id",
            unique=True,
            postgresql_where=text("claim_line_id IS NOT NULL"),
        ),
        Index("ix_claim_adjustments_tenant_id_status", "tenant_id", "status", "id"),
        _fk("claim_adjustments", "included_in_batch_id", "claim_batches"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("claim_adjustments"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    adjustment_type: Mapped[str] = mapped_column(Text)
    claim_line_id: Mapped[UUID | None] = mapped_column()  # FK to claim_lines arrives with S7
    calculation_result_id: Mapped[UUID | None] = mapped_column()
    variance_case_id: Mapped[UUID | None] = mapped_column()  # FK to variance_cases arrives with S9
    run_id: Mapped[UUID | None] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="proposed")
    included_in_batch_id: Mapped[UUID | None] = mapped_column()
    proposed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_claim_adjustments_users_proposed")
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_claim_adjustments_users_approved")
    )
    approved_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ClaimExportTemplate(Base):
    __tablename__ = "claim_export_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_claim_export_templates_tenant_id_id"),
        UniqueConstraint("tenant_id", "manufacturer_id", "name", name="uq_claim_export_templates_name"),
        _fk("claim_export_templates", "manufacturer_id", "manufacturers"),
        Index("ix_claim_export_templates_tenant_id_manufacturer_id", "tenant_id", "manufacturer_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("claim_export_templates"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    active: Mapped[bool] = mapped_column(server_default="true")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_claim_export_templates_users"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ClaimBatch(Base):
    __tablename__ = "claim_batches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_claim_batches_tenant_id_id"),
        UniqueConstraint("tenant_id", "batch_number", name="uq_claim_batches_batch_number"),
        _fk("claim_batches", "manufacturer_id", "manufacturers"),
        ForeignKeyConstraint(
            ["tenant_id", "export_template_id"],
            ["claim_export_templates.tenant_id", "claim_export_templates.id"],
            name="fk_claim_batches_claim_export_templates",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "exported_file_id"],
            ["file_objects.tenant_id", "file_objects.id"],
            name="fk_claim_batches_file_objects",
        ),
        Index("ix_claim_batches_tenant_id_status_id", "tenant_id", "status", "id"),
        Index("ix_claim_batches_tenant_id_manufacturer_id", "tenant_id", "manufacturer_id", "id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("claim_batches"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    batch_number: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    line_count: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    export_template_id: Mapped[UUID | None] = mapped_column()
    exported_file_id: Mapped[UUID | None] = mapped_column()
    submitted_at: Mapped[date | None] = mapped_column(Date)
    submitted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_claim_batches_users_submitted")
    )
    external_reference: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_claim_batches_users_created"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_claim_batches_users_approved")
    )
    approved_at: Mapped[datetime | None] = mapped_column(TS)
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(TS)


class ClaimLine(Base):
    __tablename__ = "claim_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_claim_lines_tenant_id_id"),
        UniqueConstraint("tenant_id", "claim_reference", name="uq_claim_lines_claim_reference"),
        _fk("claim_lines", "batch_id", "claim_batches"),
        _fk("claim_lines", "sales_document_line_id", "sales_document_lines"),
        _fk("claim_lines", "rebate_accrual_entry_id", "rebate_accrual_entries"),
        _fk("claim_lines", "calculation_result_id", "calculation_results"),
        _fk("claim_lines", "agreement_version_id", "agreement_versions"),
        Index(
            "uq_claim_lines_active_sales_line",
            "tenant_id",
            "sales_document_line_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND sales_document_line_id IS NOT NULL"),
        ),
        Index(
            "uq_claim_lines_active_accrual_entry",
            "tenant_id",
            "rebate_accrual_entry_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND rebate_accrual_entry_id IS NOT NULL"),
        ),
        Index("ix_claim_lines_tenant_id_batch_id", "tenant_id", "batch_id", "id"),
        Index("ix_claim_lines_tenant_id_calculation_result_id", "tenant_id", "calculation_result_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("claim_lines"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    batch_id: Mapped[UUID] = mapped_column()
    claim_reference: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(Text)
    sales_document_line_id: Mapped[UUID | None] = mapped_column()
    rebate_accrual_entry_id: Mapped[UUID | None] = mapped_column()
    calculation_result_id: Mapped[UUID] = mapped_column()
    agreement_version_id: Mapped[UUID] = mapped_column()
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    claim_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    frozen_at: Mapped[datetime | None] = mapped_column(TS)
    released_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
