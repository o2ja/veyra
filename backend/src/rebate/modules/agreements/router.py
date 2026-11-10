"""Agreements API (API_SPECIFICATION.md §2.5). Thin: validate, authorise, call the service, shape.

Permissions (SECURITY.md §3): reads ``data.read``; create/edit/validate/import/submit ``agreement.draft``;
approve/reject/return/cancel ``agreement.approve``. ``POST …/approve`` needs an ``Idempotency-Key``;
``PUT`` and ``items:import`` need ``If-Match: "<revision>"``.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from rebate.core.access import require, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules import ingestion
from rebate.modules.agreements import items_import, service
from rebate.modules.agreements import schemas as s
from rebate.modules.agreements.models import Agreement, AgreementDraft
from rebate.modules.masterdata import Who, page

router = APIRouter(tags=["agreements"])

READ = "data.read"
DRAFT = "agreement.draft"
APPROVE = "agreement.approve"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")
DraftStatus = Literal["draft", "pending_approval", "approved", "rejected", "needs_manual_entry", "cancelled"]


def _who(request: Request, principal: Principal) -> Who:
    return Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


def _revision(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the revision you edited.")
    return int(value)


def _draft_out(response: Response, d: AgreementDraft) -> s.DraftOut:
    response.headers["ETag"] = f'"{d.revision}"'
    return s.DraftOut.model_validate(d)


def _agreement_out(a: Agreement, current: dict[UUID, Any]) -> s.AgreementOut:
    v = current.get(a.id)
    return s.AgreementOut(
        id=a.id,
        manufacturer_id=a.manufacturer_id,
        agreement_type=a.agreement_type,
        external_number=a.external_number,
        name=a.name,
        cancelled_at=a.cancelled_at,
        created_by=a.created_by,
        created_at=a.created_at,
        current_version=s.VersionSummary.model_validate(v) if v is not None else None,
    )


# ---- agreements and versions ----------------------------------------------------------------------------


@router.get(
    "/agreements",
    operation_id="agreement_list",
    dependencies=[Depends(only_query_params("manufacturer_id", "agreement_type", *PAGED))],
)
def list_agreements(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    manufacturer_id: UUID | None = None,
    agreement_type: Literal["spa", "rebate"] | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.AgreementList:
    db, tenant = ctx(request).db, tenant_of(principal)
    rows = service.list_agreements(
        db, tenant, manufacturer_id=manufacturer_id, agreement_type=agreement_type, limit=limit, cursor=cursor
    )
    data, p = page(rows, limit)
    current = service.current_versions(db, tenant, [a.id for a in data])
    return s.AgreementList(data=[_agreement_out(a, current) for a in data], page=p)


@router.get("/agreements/{agreement_id}", operation_id="agreement_get")
def get_agreement(
    request: Request, agreement_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.AgreementOut:
    db, tenant = ctx(request).db, tenant_of(principal)
    a = service.get_agreement(db, tenant, agreement_id)
    return _agreement_out(a, service.current_versions(db, tenant, [a.id]))


@router.get(
    "/agreements/{agreement_id}/versions",
    operation_id="agreement_version_list",
    dependencies=[Depends(only_query_params(*PAGED))],
)
def list_versions(
    request: Request,
    agreement_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.VersionList:
    rows = service.list_versions(ctx(request).db, tenant_of(principal), agreement_id, limit, cursor)
    data, p = page(rows, limit)
    return s.VersionList(data=[s.VersionSummary.model_validate(v) for v in data], page=p)


@router.post("/agreements/{agreement_id}/cancel", operation_id="agreement_cancel")
def cancel_agreement(
    request: Request,
    agreement_id: UUID,
    body: s.CancelIn,
    principal: Annotated[Principal, Depends(require(APPROVE))],
) -> s.AgreementOut:
    db = ctx(request).db
    a = service.cancel_agreement(db, _who(request, principal), agreement_id, body.effective_date, body.reason)
    return _agreement_out(a, service.current_versions(db, a.tenant_id, [a.id]))


@router.get("/agreement-versions/{version_id}", operation_id="agreement_version_get")
def get_version(
    request: Request, version_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.VersionOut:
    return s.VersionOut.model_validate(
        service.version_detail(ctx(request).db, tenant_of(principal), version_id)
    )


@router.get(
    "/agreement-versions/{version_id}/items",
    operation_id="agreement_version_items",
    dependencies=[Depends(only_query_params(*PAGED))],
)
def list_items(
    request: Request,
    version_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ItemList:
    rows = service.list_items(ctx(request).db, tenant_of(principal), version_id, limit, cursor)
    data, p = page(rows, limit, id_of=lambda r: r[0].id)
    out = [
        s.ItemOut(
            sku=sku,
            **{
                f: getattr(i, f)
                for f in ("id", "line_number", "product_id", "customer_id", "claim_uom", *service.ITEM_MONEY)
            },
        )
        for i, sku in data
    ]
    return s.ItemList(data=out, page=p)


@router.get("/agreement-versions/{version_id}/diff/{other_version_id}", operation_id="agreement_version_diff")
def diff_versions(
    request: Request,
    version_id: UUID,
    other_version_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
) -> s.DiffOut:
    changes = service.diff(ctx(request).db, tenant_of(principal), version_id, other_version_id)
    return s.DiffOut(
        from_version_id=version_id, to_version_id=other_version_id, changes=[s.Change(**c) for c in changes]
    )


# ---- drafts ---------------------------------------------------------------------------------------------


@router.post("/agreement-drafts", status_code=201, operation_id="agreement_draft_create")
def create_draft(
    request: Request,
    response: Response,
    body: s.DraftCreate,
    principal: Annotated[Principal, Depends(require(DRAFT))],
) -> s.DraftOut:
    d = service.create_draft(
        ctx(request).db,
        _who(request, principal),
        source=body.source,
        agreement_id=body.agreement_id,
        amends_version_id=body.amends_version_id,
        payload=body.payload,
        source_file_id=body.source_file_id,
    )
    return _draft_out(response, d)


@router.get(
    "/agreement-drafts",
    operation_id="agreement_draft_list",
    dependencies=[Depends(only_query_params("status", "agreement_id", *PAGED))],
)
def list_drafts(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    status: Annotated[list[DraftStatus] | None, Query()] = None,
    agreement_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.DraftList:
    rows = service.list_drafts(
        ctx(request).db,
        tenant_of(principal),
        status=list(status) if status else None,
        agreement_id=agreement_id,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.DraftList(data=[s.DraftSummary.model_validate(d) for d in data], page=p)


@router.get("/agreement-drafts/{draft_id}", operation_id="agreement_draft_get")
def get_draft(
    request: Request,
    response: Response,
    draft_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
) -> s.DraftOut:
    return _draft_out(response, service.get_draft(ctx(request).db, tenant_of(principal), draft_id))


@router.put(
    "/agreement-drafts/{draft_id}",
    operation_id="agreement_draft_update",
    responses={412: {"description": "Revision mismatch"}},
)
def update_draft(
    request: Request,
    response: Response,
    draft_id: UUID,
    body: s.DraftUpdate,
    principal: Annotated[Principal, Depends(require(DRAFT))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.DraftOut:
    d = service.update_draft(
        ctx(request).db,
        _who(request, principal),
        draft_id,
        _revision(if_match),
        body.payload,
        body.change_summary,
    )
    return _draft_out(response, d)


@router.post("/agreement-drafts/{draft_id}/validate", operation_id="agreement_draft_validate")
def validate_draft(
    request: Request,
    response: Response,
    draft_id: UUID,
    principal: Annotated[Principal, Depends(require(DRAFT))],
) -> s.DraftOut:
    return _draft_out(response, service.validate_draft(ctx(request).db, _who(request, principal), draft_id))


@router.post(
    "/agreement-drafts/{draft_id}/items:import",
    operation_id="agreement_draft_items_import",
    responses={412: {"description": "Revision mismatch"}},
)
def import_items(
    request: Request,
    response: Response,
    draft_id: UUID,
    body: s.ItemsImportIn,
    principal: Annotated[Principal, Depends(require(DRAFT))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.ItemsImportOut:
    c = ctx(request)
    items, d = items_import.import_items(
        c.db, c.settings, _who(request, principal), draft_id, _revision(if_match), body
    )
    return s.ItemsImportOut(
        dry_run=body.dry_run,
        rows=len(items),
        items=items[:100] if body.dry_run else [],
        draft=_draft_out(response, d) if d is not None else None,
    )


@router.post("/agreement-drafts/{draft_id}/submit", operation_id="agreement_draft_submit")
def submit_draft(
    request: Request,
    response: Response,
    draft_id: UUID,
    body: s.SubmitIn,
    principal: Annotated[Principal, Depends(require(DRAFT))],
) -> s.DraftOut:
    d = service.submit(ctx(request).db, _who(request, principal), draft_id, body.acknowledged_warnings)
    return _draft_out(response, d)


@router.post("/agreement-drafts/{draft_id}/return", operation_id="agreement_draft_return")
def return_draft(
    request: Request,
    response: Response,
    draft_id: UUID,
    body: s.ReturnIn,
    principal: Annotated[Principal, Depends(require(APPROVE))],
) -> s.DraftOut:
    d = service.return_draft(ctx(request).db, _who(request, principal), draft_id, body.comment)
    return _draft_out(response, d)


@router.post("/agreement-drafts/{draft_id}/reject", operation_id="agreement_draft_reject")
def reject_draft(
    request: Request,
    response: Response,
    draft_id: UUID,
    body: s.RejectIn,
    principal: Annotated[Principal, Depends(require(APPROVE))],
) -> s.DraftOut:
    d = service.reject(ctx(request).db, _who(request, principal), draft_id, body.reason)
    return _draft_out(response, d)


@router.post(
    "/agreement-drafts/{draft_id}/approve",
    operation_id="agreement_draft_approve",
    responses={409: {"description": "Invalid state or idempotency conflict"}},
)
def approve_draft(
    request: Request,
    draft_id: UUID,
    principal: Annotated[Principal, Depends(require(APPROVE))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> s.DraftOut:
    c, who = ctx(request), _who(request, principal)
    key = ingestion.parse_key(idempotency_key)
    endpoint, body = "POST /agreement-drafts/{id}/approve", {"draft_id": str(draft_id)}
    replayed = ingestion.replay(c.db, who.tenant_id, key, endpoint, body)
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])  # type: ignore[return-value]
    d = service.approve(c.db, who, draft_id)
    out = s.DraftOut.model_validate(d)
    ingestion.store(c.db, who.tenant_id, key, endpoint, body, 200, out.model_dump(mode="json"))
    return out
