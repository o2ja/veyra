"""GET /audit-log (API_SPECIFICATION.md §2.9). Readable by admin, approver, viewer (`audit.read`)."""

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from rebate.core.access import require, tenant_of
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules.audit import service


class AuditEntry(BaseModel):
    id: str
    occurred_at: datetime
    actor_type: str
    actor_user_id: UUID | None
    actor_name: str | None
    actor_email: str | None
    action: str
    entity_type: str
    entity_id: UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    ip: str | None


class Page(BaseModel):
    next_cursor: str | None
    has_more: bool


class AuditList(BaseModel):
    data: list[AuditEntry]
    page: Page


router = APIRouter(tags=["audit"])

_PARAMS = ("entity_type", "entity_id", "actor_user_id", "action", "date_from", "date_to", "limit", "cursor")


@router.get(
    "/audit-log",
    operation_id="audit_log_list",
    dependencies=[Depends(only_query_params(*_PARAMS))],
)
def list_audit_log(
    request: Request,
    principal: Annotated[Principal, Depends(require("audit.read"))],
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
) -> AuditList:
    rows, next_cursor = service.search(
        ctx(request).db,
        tenant_of(principal),
        service.AuditQuery(
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            action=action,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            cursor=cursor,
        ),
    )
    db = ctx(request).db
    labels = request.app.state.actor_labels(db, {r.actor_user_id for r in rows if r.actor_user_id})
    return AuditList(
        data=[
            AuditEntry(
                id=str(r.id),
                occurred_at=r.occurred_at,
                actor_type=r.actor_type,
                actor_user_id=r.actor_user_id,
                actor_name=labels.get(r.actor_user_id, (None, None))[0],
                actor_email=labels.get(r.actor_user_id, (None, None))[1],
                action=r.action,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                before=r.before,
                after=r.after,
                request_id=r.request_id,
                ip=str(r.ip) if r.ip else None,
            )
            for r in rows
        ],
        page=Page(next_cursor=next_cursor, has_more=next_cursor is not None),
    )
