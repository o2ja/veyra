"""audit_log (append-only, RLS; DATABASE_SCHEMA.md §3.2) and idempotency_keys. Mirror of migrations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_audit_log_tenant_id_id"),
        Index("ix_audit_log_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_audit_log_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_audit_log_actor", "tenant_id", "actor_user_id", "occurred_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_audit_log_tenants"))
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(TS)
    actor_type: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_audit_log_users"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[UUID | None] = mapped_column()
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)


class IdempotencyKey(Base):
    """Table only in S1; no S1 endpoint requires an Idempotency-Key (API_SPECIFICATION.md §1)."""

    __tablename__ = "idempotency_keys"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_idempotency_keys_tenants"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    expires_at: Mapped[datetime] = mapped_column(TS)
