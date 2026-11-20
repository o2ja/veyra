"""identity tables (global, no RLS; DATABASE_SCHEMA.md §3.1). Migrations are the source of truth;
these mirror them so ``alembic check`` detects drift."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, LargeBinary, Text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base
from rebate.core.types import CIText

TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(CIText(), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(TS)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    mfa_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    mfa_last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default="0")
    lockout_level: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    last_failed_at: Mapped[datetime | None] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
    password_changed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # SHA-256 hex of the opaque token
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_sessions_users"))
    active_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", name="fk_sessions_tenants")
    )
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    idle_expires_at: Mapped[datetime] = mapped_column(TS)
    absolute_expires_at: Mapped[datetime] = mapped_column(TS)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, server_default="false")
    reauthenticated_at: Mapped[datetime | None] = mapped_column(TS)
    mfa_failed_count: Mapped[int] = mapped_column(Integer, server_default="0")
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class UserToken(Base):
    __tablename__ = "user_tokens"
    __table_args__ = (Index("ix_user_tokens_user_id_purpose", "user_id", "purpose"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_user_tokens_users"))
    purpose: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(Text, unique=True)  # minted by the email job
    new_email: Mapped[str | None] = mapped_column(CIText())
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (Index("ix_mfa_recovery_codes_user_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_mfa_recovery_codes_users"))
    code_hash: Mapped[str] = mapped_column(Text, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"
    __table_args__ = (Index("ix_rate_limit_counters_expires_at", "expires_at"),)

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(TS)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(TS)


class PendingRegistration(Base):
    __tablename__ = "pending_registrations"
    __table_args__ = (Index("ix_pending_registrations_email", "email"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(CIText())
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    workspace_name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(Text, unique=True)  # minted by the email job
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class PasswordResetRequest(Base):
    """Forgot-password request awaiting its job; the job payload carries only this id."""

    __tablename__ = "password_reset_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(CIText())
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class TrustedDevice(Base):
    """ "Trust this device" 2FA bypass: only the SHA-256 of the cookie token is stored."""

    __tablename__ = "trusted_devices"
    __table_args__ = (Index("ix_trusted_devices_user_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_trusted_devices_users"))
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", name="fk_trusted_devices_tenants")
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    expires_at: Mapped[datetime] = mapped_column(TS)
