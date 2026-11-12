"""Recovery queue, cases and notifications (API_SPECIFICATION.md §2.8). Thin: validate, authorise, call the service,
shape. Changes to a case need ``If-Match: "<revision>"`` (412 otherwise); every response carries the new ETag."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from rebate.core.access import require, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules.masterdata import Page, Who, page
from rebate.modules.recovery import schemas as s
from rebate.modules.recovery import service
from rebate.modules.recovery.domain.states import CaseStatus

router = APIRouter(tags=["recovery"])

READ = "data.read"
WORK = "case.work"
WRITE_OFF = "case.write_off"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")
NO_QUERY = Depends(only_query_params())
IfMatch = Annotated[str | None, Header()]


def _who(request: Request, principal: Principal) -> Who:
    return Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


def _revision(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the revision you saw.")
    return int(value)


def _out(response: Response, c: object) -> s.CaseOut:
    out = s.CaseOut.model_validate(c)
    response.headers["ETag"] = f'"{out.revision}"'
    return out


@router.get(
    "/cases",
    operation_id="case_list",
    dependencies=[
        Depends(
            only_query_params(
                "status", "manufacturer_id", "owner_user_id", "deadline_before", "reason_code", *PAGED
            )
        )
    ],
)
def list_cases(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    status: Annotated[list[s.CaseStatus] | None, Query()] = None,
    manufacturer_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    deadline_before: date | None = None,
    reason_code: Annotated[str | None, Query(max_length=63)] = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.CaseList:
    rows, nxt = service.list_cases(
        ctx(request).db,
        tenant_of(principal),
        statuses=list(status) if status else None,
        manufacturer_id=manufacturer_id,
        owner_user_id=owner_user_id,
        deadline_before=deadline_before,
        reason_code=reason_code,
        limit=limit,
        cursor=cursor,
    )
    return s.CaseList(
        data=[s.CaseOut.model_validate(r) for r in rows], page=Page(next_cursor=nxt, has_more=nxt is not None)
    )


@router.get("/cases/summary", operation_id="case_summary", dependencies=[NO_QUERY])
def summary(request: Request, principal: Annotated[Principal, Depends(require(READ))]) -> s.SummaryOut:
    return s.SummaryOut(**service.summary(ctx(request).db, tenant_of(principal)))


@router.get("/cases/{case_id}", operation_id="case_get", dependencies=[NO_QUERY])
def get_case(
    request: Request,
    response: Response,
    case_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
) -> s.CaseOut:
    return _out(response, service.get(ctx(request).db, tenant_of(principal), case_id))


@router.get("/cases/{case_id}/events", operation_id="case_event_list", dependencies=[NO_QUERY])
def list_events(
    request: Request, case_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.EventList:
    rows = service.list_events(ctx(request).db, tenant_of(principal), case_id)
    return s.EventList(data=[s.EventOut.model_validate(r) for r in rows])


@router.get("/cases/{case_id}/attachments", operation_id="case_attachment_list", dependencies=[NO_QUERY])
def list_attachments(
    request: Request, case_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> list[s.AttachmentOut]:
    rows = service.list_attachments(ctx(request).db, tenant_of(principal), case_id)
    return [s.AttachmentOut.model_validate(r) for r in rows]


@router.patch("/cases/{case_id}", operation_id="case_update")
def update_case(
    request: Request,
    response: Response,
    case_id: UUID,
    body: s.CasePatch,
    principal: Annotated[Principal, Depends(require(WORK))],
    if_match: IfMatch = None,
) -> s.CaseOut:
    c = service.assign(
        ctx(request).db, _who(request, principal), case_id, body.owner_user_id, _revision(if_match)
    )
    return _out(response, c)


@router.post("/cases/{case_id}/transition", operation_id="case_transition")
def transition(
    request: Request,
    response: Response,
    case_id: UUID,
    body: s.TransitionIn,
    principal: Annotated[Principal, Depends(require(WORK))],
    if_match: IfMatch = None,
) -> s.CaseOut:
    c = service.transition(
        ctx(request).db,
        _who(request, principal),
        case_id,
        CaseStatus(body.to_status),
        body.note,
        _revision(if_match),
    )
    return _out(response, c)


@router.post("/cases/{case_id}/notes", status_code=201, operation_id="case_note_add")
def add_note(
    request: Request, case_id: UUID, body: s.NoteIn, principal: Annotated[Principal, Depends(require(WORK))]
) -> s.EventOut:
    return s.EventOut.model_validate(
        service.add_note(ctx(request).db, _who(request, principal), case_id, body.note)
    )


@router.post("/cases/{case_id}/attachments", status_code=201, operation_id="case_attachment_add")
def add_attachment(
    request: Request,
    case_id: UUID,
    body: s.AttachmentIn,
    principal: Annotated[Principal, Depends(require(WORK))],
) -> s.AttachmentOut:
    a = service.add_attachment(ctx(request).db, _who(request, principal), case_id, body.file_id)
    return s.AttachmentOut.model_validate(a)


@router.post("/cases/{case_id}/write-off", operation_id="case_write_off")
def write_off(
    request: Request,
    response: Response,
    case_id: UUID,
    body: s.WriteOffIn,
    principal: Annotated[Principal, Depends(require(WRITE_OFF))],
    if_match: IfMatch = None,
) -> s.CaseOut:
    c = service.write_off(
        ctx(request).db, _who(request, principal), case_id, body.reason, _revision(if_match)
    )
    return _out(response, c)


@router.post("/cases/{case_id}/reclaim", operation_id="case_reclaim")
def reclaim(
    request: Request,
    response: Response,
    case_id: UUID,
    principal: Annotated[Principal, Depends(require(WORK))],
    if_match: IfMatch = None,
) -> s.ReclaimOut:
    c, adjustment_id = service.reclaim(
        ctx(request).db, _who(request, principal), case_id, _revision(if_match)
    )
    return s.ReclaimOut(case=_out(response, c), adjustment_id=adjustment_id)


@router.get(
    "/notifications",
    operation_id="notification_list",
    dependencies=[Depends(only_query_params("unread", *PAGED))],
)
def list_notifications(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    unread: bool = False,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.NotificationList:
    rows = service.list_notifications(
        ctx(request).db, tenant_of(principal), principal.user_id, unread=unread, limit=limit, cursor=cursor
    )
    data, p = page(rows, limit)
    return s.NotificationList(data=[s.NotificationOut.model_validate(r) for r in data], page=p)


@router.post(
    "/notifications/{notification_id}/read", operation_id="notification_read", dependencies=[NO_QUERY]
)
def read_notification(
    request: Request, notification_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.NotificationOut:
    n = service.mark_read(ctx(request).db, tenant_of(principal), principal.user_id, notification_id)
    return s.NotificationOut.model_validate(n)
