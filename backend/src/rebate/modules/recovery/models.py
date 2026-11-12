"""Recovery tables (RLS; DATABASE_SCHEMA.md §3.10). Mirror of migration 20261118_0900 for ``alembic check``.
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from rebate.core.db import Base

TS = DateTime(timezone=True)
LIVE = "status NOT IN ('recovered', 'written_off', 'closed_no_action')"


def _tenant_fk(table: str) -> ForeignKey:
    return ForeignKey("tenants.id", name=f"fk_{table}_tenants")


def _fk(table: str, column: str, target: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=f"fk_{table}_{target}"
    )


def _user(suffix: str) -> ForeignKey:
    return ForeignKey("users.id", name=f"fk_variance_cases_users_{suffix}")


class VarianceCase(Base):
    __tablename__ = "variance_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_variance_cases_tenant_id_id"),
        _fk("variance_cases", "manufacturer_id", "manufacturers"),
        _fk("variance_cases", "claim_line_id", "claim_lines"),
        _fk("variance_cases", "remittance_line_id", "remittance_lines"),
        _fk("variance_cases", "remittance_document_id", "remittance_documents"),
        Index(
            "uq_variance_cases_live_claim_line",
            "tenant_id",
            "claim_line_id",
            "case_type",
            unique=True,
            postgresql_where=text(f"claim_line_id IS NOT NULL AND {LIVE}"),
        ),
        Index(
            "uq_variance_cases_live_remittance_line",
            "tenant_id",
            "remittance_line_id",
            "case_type",
            unique=True,
            postgresql_where=text(f"claim_line_id IS NULL AND {LIVE}"),
        ),
        Index("ix_variance_cases_tenant_id_status_deadline_date", "tenant_id", "status", "deadline_date"),
        Index("ix_variance_cases_tenant_id_manufacturer_id", "tenant_id", "manufacturer_id", "id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("variance_cases"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[UUID] = mapped_column()
    claim_line_id: Mapped[UUID | None] = mapped_column()
    remittance_line_id: Mapped[UUID | None] = mapped_column()
    remittance_document_id: Mapped[UUID | None] = mapped_column()
    case_type: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(
        ForeignKey("reason_codes.code", name="fk_variance_cases_reason_codes")
    )
    external_reason_code: Mapped[str | None] = mapped_column(Text)
    claimed_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    variance_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    deadline_date: Mapped[date | None] = mapped_column(Date)
    deadline_basis: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[UUID | None] = mapped_column(_user("owner"))
    status: Mapped[str] = mapped_column(Text, server_default="open")
    recovered_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    written_off_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    dispute_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    write_off_proposed_by: Mapped[UUID | None] = mapped_column(_user("proposer"))
    write_off_proposed_at: Mapped[datetime | None] = mapped_column(TS)
    write_off_reason: Mapped[str | None] = mapped_column(Text)
    written_off_by: Mapped[UUID | None] = mapped_column(_user("written_off"))
    written_off_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[UUID | None] = mapped_column(_user("created"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    closed_at: Mapped[datetime | None] = mapped_column(TS)


class VarianceCaseEvent(Base):
    __tablename__ = "variance_case_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_variance_case_events_tenant_id_id"),
        _fk("variance_case_events", "case_id", "variance_cases"),
        Index("ix_variance_case_events_tenant_id_case_id", "tenant_id", "case_id", "created_at", "id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("variance_case_events"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(Text)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_variance_case_events_users")
    )
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class CaseAttachment(Base):
    __tablename__ = "case_attachments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_case_attachments_tenant_id_id"),
        UniqueConstraint("tenant_id", "case_id", "file_id", name="uq_case_attachments_case_file"),
        _fk("case_attachments", "case_id", "variance_cases"),
        _fk("case_attachments", "file_id", "file_objects"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("case_attachments"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[UUID] = mapped_column()
    added_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_case_attachments_users"))
    added_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_notifications_tenant_id_id"),
        UniqueConstraint("tenant_id", "user_id", "dedupe_key", name="uq_notifications_dedupe"),
        Index("ix_notifications_tenant_id_user_id", "tenant_id", "user_id", text("created_at DESC"), "id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(_tenant_fk("notifications"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_notifications_users"))
    kind: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[UUID | None] = mapped_column()
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    dedupe_key: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(TS)
    emailed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
