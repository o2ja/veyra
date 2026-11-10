"""Workspace settings and invitations (API_SPECIFICATION.md §2.1)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from rebate.core.access import require, require_recent_reauth, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.request import Principal, ctx
from rebate.core.validation import STRICT, Email
from rebate.modules.tenancy import service

Role = Literal["admin", "approver", "analyst", "viewer"]

router = APIRouter(tags=["workspace"])


class SettingsOut(BaseModel):
    business_timezone: str
    currency_code: str
    mfa_required: bool
    four_eyes_required: bool
    ai_processing_enabled: bool
    retention_years: int
    revision: int


class SettingsPatch(BaseModel):
    model_config = STRICT

    business_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    mfa_required: bool | None = None
    four_eyes_required: bool | None = None
    ai_processing_enabled: bool | None = None
    retention_years: int | None = Field(default=None, ge=1, le=30)


def _settings_out(row: object) -> SettingsOut:
    return SettingsOut.model_validate(row, from_attributes=True)


@router.get("/settings", operation_id="settings_get")
def get_settings(
    request: Request, response: Response, principal: Annotated[Principal, Depends(require("settings.read"))]
) -> SettingsOut:
    row = service.get_settings(ctx(request).db, tenant_of(principal))
    response.headers["ETag"] = f'"{row.revision}"'
    return _settings_out(row)


def _revision(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the revision you edited.")
    return int(value)


@router.patch(
    "/settings", operation_id="settings_update", responses={412: {"description": "Revision mismatch"}}
)
def patch_settings(
    request: Request,
    response: Response,
    body: SettingsPatch,
    principal: Annotated[Principal, Depends(require("settings.write"))],
    if_match: Annotated[str | None, Header()] = None,
) -> SettingsOut:
    c = ctx(request)
    row = service.update_settings(
        c.db,
        tenant_id=tenant_of(principal),
        actor_id=principal.user_id,
        expected_revision=_revision(if_match),
        changes=body.model_dump(exclude_none=True),
        meta=c.audit_meta(),
    )
    response.headers["ETag"] = f'"{row.revision}"'
    return _settings_out(row)


class InvitationIn(BaseModel):
    model_config = STRICT

    email: Email
    role: Role


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    invited_by: UUID
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationList(BaseModel):
    data: list[InvitationOut]


@router.post("/invitations", status_code=201, operation_id="invitation_create")
def create_invitation(
    request: Request,
    body: InvitationIn,
    principal: Annotated[Principal, Depends(require("invitation.manage"))],
) -> InvitationOut:
    c = ctx(request)
    if body.role == "admin":  # granting admin is a step-up action (SECURITY.md §1)
        require_recent_reauth(principal, c.settings.step_up_window_minutes)
    tenant_id = tenant_of(principal)
    inv = service.create_invitation(
        c.db,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        email=body.email,
        role=body.role,
        days=c.settings.invitation_days,
        meta=c.audit_meta(),
    )
    return InvitationOut.model_validate(inv)


@router.get("/invitations", operation_id="invitation_list")
def list_invitations(
    request: Request, principal: Annotated[Principal, Depends(require("invitation.manage"))]
) -> InvitationList:
    rows = service.list_invitations(ctx(request).db, tenant_of(principal))
    return InvitationList(data=[InvitationOut.model_validate(r) for r in rows])


@router.delete("/invitations/{invitation_id}", status_code=204, operation_id="invitation_revoke")
def revoke_invitation(
    request: Request,
    invitation_id: UUID,
    principal: Annotated[Principal, Depends(require("invitation.manage"))],
) -> Response:
    c = ctx(request)
    service.revoke_invitation(
        c.db,
        tenant_id=tenant_of(principal),
        actor_id=principal.user_id,
        invitation_id=invitation_id,
        meta=c.audit_meta(),
    )
    return Response(status_code=204)
