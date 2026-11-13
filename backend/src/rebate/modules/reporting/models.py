"""Reporting tables (RLS; DATABASE_SCHEMA.md §3.11). Mirror of migration 20261119_0900 for ``alembic check``."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, Index, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)
_ZERO = "'00000000-0000-0000-0000-000000000000'"


class SummaryClaimsMonthly(Base):
    __tablename__ = "summary_claims_monthly"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_summary_claims_monthly_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "manufacturer_id"],
            ["manufacturers.tenant_id", "manufacturers.id"],
            name="fk_summary_claims_monthly_manufacturers",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "branch_id"],
            ["branches.tenant_id", "branches.id"],
            name="fk_summary_claims_monthly_branches",
        ),
        Index(
            "uq_summary_claims_monthly_key",
            "tenant_id",
            "manufacturer_id",
            text(f"coalesce(branch_id, {_ZERO})"),
            "month",
            unique=True,
        ),
        Index("ix_summary_claims_monthly_tenant_id_month", "tenant_id", "month"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_summary_claims_monthly_tenants")
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    branch_id: Mapped[UUID | None] = mapped_column()
    month: Mapped[date] = mapped_column(Date)
    claimed: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    collected: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    short_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    recovered: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    written_off: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    at_risk: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    expiring: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    as_of_date: Mapped[date] = mapped_column(Date)
    refreshed_at: Mapped[datetime] = mapped_column(TS)
