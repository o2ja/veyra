"""/auth/*, /users, /invitations/accept (API_SPECIFICATION.md §2.1). Routers only parse, check access,
call a service and shape the response; cookies are set here because they are HTTP concerns."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from rebate.core.access import authenticated, public, require, require_recent_reauth, tenant_of
from rebate.core.request import Principal, ctx
from rebate.core.security import new_token
from rebate.core.validation import STRICT, Email, JsonUUID, Name, Token, TotpCode
from rebate.modules import tenancy
from rebate.modules.identity import service, sessions

router = APIRouter(tags=["auth"])

Password = Annotated[str, Field(min_length=1, max_length=1024)]
Role = Literal["admin", "approver", "analyst", "viewer"]


class Accepted(BaseModel):
    status: Literal["accepted"] = "accepted"
    message: str


class Ok(BaseModel):
    status: Literal["ok"] = "ok"


# ---- public -------------------------------------------------------------------------------------


@router.get("/auth/csrf", status_code=204, operation_id="auth_csrf", dependencies=[Depends(public())])
def csrf(request: Request) -> Response:
    """Pre-session CSRF token for public POSTs (login CSRF protection). Signed-in sessions get theirs
    with the session cookie."""
    response = Response(status_code=204)
    if ctx(request).principal is None:
        response.set_cookie(
            sessions.CSRF_COOKIE, new_token(), path="/", secure=True, httponly=False, samesite="strict"
        )
    return response


class RegisterIn(BaseModel):
    model_config = STRICT
    email: Email
    full_name: Name
    workspace_name: Name
    password: Password


GENERIC_EMAIL = "If the address can be used, we sent an email with the next step."


@router.post(
    "/auth/register", status_code=202, operation_id="auth_register", dependencies=[Depends(public())]
)
def register(request: Request, body: RegisterIn) -> Accepted:
    service.register(
        ctx(request),
        email=body.email,
        full_name=body.full_name,
        workspace_name=body.workspace_name,
        password=body.password,
    )
    return Accepted(message=GENERIC_EMAIL)


class TokenIn(BaseModel):
    model_config = STRICT
    token: Token


@router.post("/auth/verify-email", operation_id="auth_verify_email", dependencies=[Depends(public())])
def verify_email(request: Request, body: TokenIn) -> Ok:
    service.verify_email(ctx(request), body.token)
    return Ok()


class LoginIn(BaseModel):
    model_config = STRICT
    email: Email
    password: Password


class LoginOut(BaseModel):
    status: Literal["signed_in", "mfa_required"]


@router.post("/auth/login", operation_id="auth_login", dependencies=[Depends(public())])
def login(request: Request, response: Response, body: LoginIn) -> LoginOut:
    c = ctx(request)
    if c.principal is not None:
        sessions.revoke(c.db, c.principal.session_id)  # no session fixation / stacking
    result = service.login(c, email=body.email, password=body.password)
    sessions.set_cookies(response, c.settings, result.session)
    return LoginOut(status="mfa_required" if result.mfa_required else "signed_in")


class ForgotIn(BaseModel):
    model_config = STRICT
    email: Email


@router.post(
    "/auth/password/forgot",
    status_code=202,
    operation_id="auth_password_forgot",
    dependencies=[Depends(public())],
)
def forgot(request: Request, body: ForgotIn) -> Accepted:
    service.forgot_password(ctx(request), body.email)
    return Accepted(message=GENERIC_EMAIL)


class ResetIn(BaseModel):
    model_config = STRICT
    token: Token
    new_password: Password


@router.post("/auth/password/reset", operation_id="auth_password_reset", dependencies=[Depends(public())])
def reset(request: Request, response: Response, body: ResetIn) -> Ok:
    service.reset_password(ctx(request), body.token, body.new_password)
    sessions.clear_cookies(response)
    return Ok()


@router.post("/auth/email/confirm", operation_id="auth_email_confirm", dependencies=[Depends(public())])
def confirm_email(request: Request, response: Response, body: TokenIn) -> Ok:
    service.confirm_email_change(ctx(request), body.token)
    sessions.clear_cookies(response)
    return Ok()


class AcceptIn(BaseModel):
    model_config = STRICT
    token: Token
    full_name: Name | None = None
    password: Password | None = None


@router.post("/invitations/accept", operation_id="invitation_accept", dependencies=[Depends(public())])
def accept_invitation(request: Request, body: AcceptIn) -> Ok:
    c = ctx(request)
    principal = c.principal if c.principal is not None and not c.principal.mfa_pending else None
    service.accept_invitation(
        c, principal, token=body.token, full_name=body.full_name, password=body.password
    )
    return Ok()


# ---- signed in ----------------------------------------------------------------------------------


class MfaIn(BaseModel):
    model_config = STRICT
    code: TotpCode


@router.post("/auth/mfa/verify", operation_id="auth_mfa_verify")
def mfa_verify(
    request: Request,
    response: Response,
    body: MfaIn,
    principal: Annotated[Principal, Depends(authenticated(allow_mfa_pending=True, allow_enrollment=True))],
) -> LoginOut:
    c = ctx(request)
    new = service.verify_mfa(c, principal, body.code)
    sessions.set_cookies(response, c.settings, new)
    return LoginOut(status="signed_in")


class MfaSetupOut(BaseModel):
    otpauth_uri: str
    qr_svg_data_uri: str


@router.post("/auth/mfa/setup", operation_id="auth_mfa_setup")
def mfa_setup(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(authenticated(allow_enrollment=True))],
) -> MfaSetupOut:
    uri, svg = service.mfa_setup(ctx(request), principal)
    return MfaSetupOut(otpauth_uri=uri, qr_svg_data_uri=svg)


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


@router.post("/auth/mfa/enable", operation_id="auth_mfa_enable")
def mfa_enable(
    request: Request,
    response: Response,
    body: MfaIn,
    principal: Annotated[Principal, Depends(authenticated(allow_enrollment=True))],
) -> RecoveryCodesOut:
    c = ctx(request)
    new, codes = service.mfa_enable(c, principal, body.code)
    sessions.set_cookies(response, c.settings, new)
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/auth/mfa/recovery-codes", operation_id="auth_mfa_recovery_codes")
def recovery_codes(
    request: Request, principal: Annotated[Principal, Depends(authenticated())]
) -> RecoveryCodesOut:
    c = ctx(request)
    require_recent_reauth(principal, c.settings.step_up_window_minutes)
    return RecoveryCodesOut(recovery_codes=service.regenerate_recovery_codes(c, principal))


@router.post("/auth/logout", status_code=204, operation_id="auth_logout")
def logout(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated(allow_mfa_pending=True, allow_enrollment=True))],
) -> Response:
    service.logout(ctx(request), principal)
    response = Response(status_code=204)
    sessions.clear_cookies(response)
    return response


class UserOut(BaseModel):
    id: UUID
    email: str
    mfa_enabled: bool


class TenantOut(BaseModel):
    id: UUID
    name: str


class MeOut(BaseModel):
    user: UserOut
    tenant: TenantOut | None
    role: str | None
    permissions: list[str]
    mfa_pending: bool
    enrollment_required: bool


@router.get("/auth/me", operation_id="auth_me")
def me(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated(allow_mfa_pending=True, allow_enrollment=True))],
) -> MeOut:
    db = ctx(request).db
    tenant = None
    if principal.tenant_id is not None and not principal.mfa_pending:
        tenant = TenantOut(id=principal.tenant_id, name=tenancy.tenant_name(db, principal.tenant_id))
    return MeOut(
        user=UserOut(id=principal.user_id, email=principal.email, mfa_enabled=principal.user_mfa_enabled),
        tenant=tenant,
        role=None if principal.mfa_pending else principal.role,
        permissions=[] if not principal.mfa_satisfied else sorted(principal.permissions),
        mfa_pending=principal.mfa_pending,
        enrollment_required=principal.enrollment_required,
    )


class WorkspaceOut(BaseModel):
    tenant_id: UUID
    name: str
    role: str


class WorkspaceList(BaseModel):
    data: list[WorkspaceOut]


@router.get("/auth/tenants", operation_id="auth_tenants")
def my_tenants(
    request: Request, principal: Annotated[Principal, Depends(authenticated(allow_enrollment=True))]
) -> WorkspaceList:
    rows = tenancy.memberships_of(ctx(request).db, principal.user_id)
    return WorkspaceList(
        data=[
            WorkspaceOut(tenant_id=r.tenant_id, name=r.tenant_name, role=r.role)
            for r in rows
            if r.status == "active"
        ]
    )


class SwitchIn(BaseModel):
    model_config = STRICT
    tenant_id: JsonUUID


@router.post("/auth/switch-tenant", operation_id="auth_switch_tenant")
def switch_tenant(
    request: Request,
    response: Response,
    body: SwitchIn,
    principal: Annotated[Principal, Depends(authenticated(allow_enrollment=True))],
) -> Ok:
    c = ctx(request)
    new = service.switch_tenant(c, principal, body.tenant_id)
    sessions.set_cookies(response, c.settings, new)
    return Ok()


class ReauthIn(BaseModel):
    model_config = STRICT
    password: Password
    mfa_code: TotpCode | None = None


@router.post("/auth/reauthenticate", operation_id="auth_reauthenticate")
def reauthenticate(
    request: Request,
    body: ReauthIn,
    principal: Annotated[Principal, Depends(authenticated(allow_enrollment=True))],
) -> Ok:
    service.reauthenticate(ctx(request), principal, body.password, body.mfa_code)
    return Ok()


class PasswordChangeIn(BaseModel):
    model_config = STRICT
    current_password: Password
    new_password: Password


@router.post("/auth/password/change", operation_id="auth_password_change")
def change_password(
    request: Request, body: PasswordChangeIn, principal: Annotated[Principal, Depends(authenticated())]
) -> Ok:
    c = ctx(request)
    require_recent_reauth(principal, c.settings.step_up_window_minutes)
    service.change_password(c, principal, body.current_password, body.new_password)
    return Ok()


class EmailChangeIn(BaseModel):
    model_config = STRICT
    new_email: Email
    password: Password
    mfa_code: TotpCode


@router.post("/auth/email/change", status_code=202, operation_id="auth_email_change")
def change_email(
    request: Request, body: EmailChangeIn, principal: Annotated[Principal, Depends(authenticated())]
) -> Accepted:
    service.request_email_change(
        ctx(request), principal, new_email=body.new_email, password=body.password, mfa_code=body.mfa_code
    )
    return Accepted(message="Check the new address for a confirmation link.")


# ---- members of the active workspace ------------------------------------------------------------


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    full_name: str
    role: str
    status: str
    mfa_enabled: bool


class MemberList(BaseModel):
    data: list[MemberOut]


@router.get("/users", operation_id="user_list")
def list_users(
    request: Request, principal: Annotated[Principal, Depends(require("user.manage"))]
) -> MemberList:
    rows = service.list_members(ctx(request), tenant_of(principal))
    return MemberList(data=[MemberOut(**r.__dict__) for r in rows])


class MemberPatch(BaseModel):
    model_config = STRICT
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None


@router.patch("/users/{user_id}", operation_id="user_update")
def update_user(
    request: Request,
    user_id: UUID,
    body: MemberPatch,
    principal: Annotated[Principal, Depends(require("user.manage"))],
) -> MemberOut:
    c = ctx(request)
    if body.role == "admin":
        require_recent_reauth(principal, c.settings.step_up_window_minutes)
    view = service.change_member(
        c, principal, tenant_of(principal), user_id, role=body.role, status=body.status
    )
    return MemberOut(**view.__dict__)
