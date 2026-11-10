"""file_objects (RLS; DATABASE_SCHEMA.md §3.2). Mirror of the migration for ``alembic check``."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)


class FileObject(Base):
    __tablename__ = "file_objects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_file_objects_tenant_id_id"),
        Index(
            "uq_file_objects_tenant_id_kind_sha256",
            "tenant_id",
            "kind",
            "sha256",
            unique=True,
            postgresql_where=text("kind IN ('import', 'remittance', 'agreement') AND scan_status <> 'error'"),
        ),
        Index("ix_file_objects_tenant_id_created_at", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", name="fk_file_objects_tenants"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    declared_mime: Mapped[str | None] = mapped_column(Text)
    detected_mime: Mapped[str] = mapped_column(Text)
    scan_status: Mapped[str] = mapped_column(Text, server_default="pending")
    scan_detail: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_file_objects_users"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    scanned_at: Mapped[datetime | None] = mapped_column(TS)
