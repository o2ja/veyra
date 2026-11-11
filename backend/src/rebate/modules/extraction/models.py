"""document_extractions and ai_invocations (RLS; DATABASE_SCHEMA.md §3.6). Mirror of migration
20261113_0900_extraction for ``alembic check``; CHECKs, triggers and grants live only in the migration."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
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


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_extractions_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "file_id"],
            ["file_objects.tenant_id", "file_objects.id"],
            name="fk_document_extractions_file_objects",
        ),
        Index(
            "uq_document_extractions_active",
            "tenant_id",
            "file_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        Index("ix_document_extractions_tenant_id_file_id", "tenant_id", "file_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_document_extractions_tenants"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    file_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    text_method: Mapped[str | None] = mapped_column(Text)
    pass_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    evidence_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_document_extractions_users"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class AiInvocation(Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ai_invocations_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_id"],
            ["document_extractions.tenant_id", "document_extractions.id"],
            name="fk_ai_invocations_document_extractions",
        ),
        Index("ix_ai_invocations_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_ai_invocations_tenant_id_extraction_id", "tenant_id", "extraction_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_ai_invocations_tenants"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    extraction_id: Mapped[UUID | None] = mapped_column()
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
