"""tenancy tables (RLS; DATABASE_SCHEMA.md §3.1). Mirror of the migrations for ``alembic check``."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CHAR, Boolean, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base
from rebate.core.types import CIText

TS = DateTime(timezone=True)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class TenantSetting(Base):
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_tenant_settings_tenants"), primary_key=True
    )
    business_timezone: Mapped[str] = mapped_column(Text, server_default="America/Chicago")
    currency_code: Mapped[str] = mapped_column(CHAR(3), server_default="USD")
    mfa_required: Mapped[bool] = mapped_column(Boolean, server_default="true")
    four_eyes_required: Mapped[bool] = mapped_column(Boolean, server_default="true")
    ai_processing_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    retention_years: Mapped[int] = mapped_column(Integer, server_default="7")
    default_claim_rounding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id_id"),
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_id_user_id"),
        Index("ix_memberships_user_id", "user_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_memberships_tenants"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_memberships_users"))
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    invited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_memberships_invited_by"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_invitations_tenant_id_id"),
        Index("ix_invitations_tenant_id_email", "tenant_id", "email"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_invitations_tenants"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(CIText())
    role: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(Text, unique=True)  # minted by the email job
    invited_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_invitations_invited_by"))
    expires_at: Mapped[datetime] = mapped_column(TS)
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    accepted_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_invitations_accepted_user")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
