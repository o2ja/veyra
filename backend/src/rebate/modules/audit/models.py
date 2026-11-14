"""audit_log and audit_seals (append-only, RLS; DATABASE_SCHEMA.md §3.2) and idempotency_keys. Mirror of migrations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import CHAR, INET, JSONB
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
    # none_as_null: Python None is stored as SQL NULL, not as the JSON scalar null
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    request_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB(none_as_null=True))


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


class AuditSeal(Base):
    """Hash-chain seal over a range of one tenant's audit_log entries (written only by ``seal.py``)."""

    __tablename__ = "audit_seals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_audit_seals_tenant_id_id"),
        UniqueConstraint("tenant_id", "from_audit_id", name="uq_audit_seals_tenant_id_from_audit_id"),
        UniqueConstraint("tenant_id", "to_audit_id", name="uq_audit_seals_tenant_id_to_audit_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_audit_seals_tenants"))
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    from_audit_id: Mapped[int] = mapped_column(BigInteger)
    to_audit_id: Mapped[int] = mapped_column(BigInteger)
    prev_hash: Mapped[str | None] = mapped_column(CHAR(64))
    seal_hash: Mapped[str] = mapped_column(CHAR(64))
    sealed_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
