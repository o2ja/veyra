"""Audit writer and search (FR-AU-01..03). The writer uses the caller's session, so the entry commits
or rolls back together with the change it records (same transaction, ADR-026)."""

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import ValidationFailed
from rebate.modules.audit.events import AUDITED_FIELDS, Action
from rebate.modules.audit.models import AuditLog


def _allowed(entity_type: str, values: dict[str, Any] | None) -> dict[str, Any] | None:
    if values is None:
        return None
    allowed = AUDITED_FIELDS.get(entity_type)
    if allowed is None:
        raise ValueError(f"entity type {entity_type!r} has no audited-field allow-list")
    return {k: _jsonable(v) for k, v in values.items() if k in allowed}


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID | datetime | date):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    return value


@dataclass(frozen=True)
class Actor:
    type: str  # user | system | ai
    user_id: UUID | None = None


def record(
    db: Session,
    *,
    tenant_id: UUID,
    actor: Actor,
    action: Action,
    entity_type: str,
    entity_id: UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one entry. ``db`` must have ``app.tenant_id`` = ``tenant_id`` (RLS WITH CHECK)."""
    meta = meta or {}
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            occurred_at=clock.now(),
            actor_type=actor.type,
            actor_user_id=actor.user_id,
            action=str(action),
            entity_type=entity_type,
            entity_id=entity_id,
            before=_allowed(entity_type, before),
            after=_allowed(entity_type, after),
            request_id=meta.get("request_id"),
            ip=meta.get("ip"),
            user_agent=(meta.get("user_agent") or "")[:512] or None,
            metadata_=metadata,
        )
    )
    db.flush()


@dataclass(frozen=True)
class AuditQuery:
    entity_type: str | None = None
    entity_id: UUID | None = None
    actor_user_id: UUID | None = None
    action: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = 50
    cursor: str | None = None


def _decode_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed(
            details=[{"field": "cursor", "code": "invalid", "message": "Invalid cursor."}]
        ) from exc


def search(db: Session, tenant_id: UUID, q: AuditQuery) -> tuple[list[AuditLog], str | None]:
    """Newest first; keyset pagination on id. RLS scopes rows; tenant_id is also filtered explicitly."""
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if q.entity_type:
        stmt = stmt.where(AuditLog.entity_type == q.entity_type)
    if q.entity_id:
        stmt = stmt.where(AuditLog.entity_id == q.entity_id)
    if q.actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == q.actor_user_id)
    if q.action:
        stmt = stmt.where(AuditLog.action == q.action)
    if q.date_from:
        stmt = stmt.where(AuditLog.occurred_at >= datetime.combine(q.date_from, time.min, UTC))
    if q.date_to:
        end = datetime.combine(q.date_to + timedelta(days=1), time.min, UTC)
        stmt = stmt.where(AuditLog.occurred_at < end)
    if q.cursor:
        stmt = stmt.where(AuditLog.id < _decode_cursor(q.cursor))
    rows = list(db.scalars(stmt.order_by(AuditLog.id.desc()).limit(q.limit + 1)))
    next_cursor = None
    if len(rows) > q.limit:
        rows = rows[: q.limit]
        next_cursor = base64.urlsafe_b64encode(str(rows[-1].id).encode()).decode()
    return rows, next_cursor
