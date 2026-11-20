"""identity flows (FR-ID-01..07, SECURITY.md §1). Run inside the request transaction (``RequestContext``).

Anti-enumeration rules applied throughout (S1 review findings 1, 11):
* public endpoints answer identically whether or not an account exists;
* both branches do the same expensive work (one Argon2 operation, one row write, at most one email);
* emails are handed to SMTP only after commit, off the response path.
"""

from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from rebate.core import clock, jobs
from rebate.core.config import Settings
from rebate.core.db import tenant_scope
from rebate.core.errors import (
    AccountLocked,
    InvalidCredentials,
    InvalidMfaCode,
    LinkExpired,
    MfaEnrollmentRequired,
    NotFound,
    Unauthenticated,
    ValidationFailed,
)
from rebate.core.ids import new_id
from rebate.core.request import Principal, RequestContext
from rebate.core.security import Passwords, SecretBox, hash_token, keyed_hash
from rebate.core.validation import email_key
from rebate.modules import audit, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.identity import lockout, mfa, passwords, ratelimit, sessions, trusted
from rebate.modules.identity.models import (
    AuthSession,
    PasswordResetRequest,
    PendingRegistration,
    User,
    UserToken,
)

log = structlog.get_logger("rebate.identity")


@lru_cache(maxsize=4)
def password_hasher(settings: Settings) -> Passwords:
    return Passwords(settings)


@lru_cache(maxsize=4)
def secret_box(settings: Settings) -> SecretBox:
    return SecretBox(settings.encryption_keys.get_secret_value())


def _hkey(c: RequestContext, purpose: str, value: str) -> str:
    return keyed_hash(c.settings.secret_key.get_secret_value(), purpose, value)[:40]


def _user_by_email(db: Session, email: str, *, lock: bool = False) -> User | None:
    stmt = select(User).where(User.email == email)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def _user(db: Session, user_id: UUID, *, lock: bool = False) -> User:
    stmt = select(User).where(User.id == user_id)
    user = db.scalar(stmt.with_for_update() if lock else stmt)
    if user is None:
        raise Unauthenticated()
    return user


def audit_user_event(
    c: RequestContext,
    user_id: UUID,
    action: Action,
    *,
    actor: Actor | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """User-level events have no single tenant: one entry per active membership (S1 D-7)."""
    for tenant_id in tenancy.active_tenant_ids(c.db, user_id):
        with tenant_scope(c.db, tenant_id):
            audit.record(
                c.db,
                tenant_id=tenant_id,
                actor=actor or Actor("user", user_id),
                action=action,
                entity_type="user",
                entity_id=user_id,
                before=before,
                after=after,
                meta=c.audit_meta(),
                metadata=metadata,
            )


# ---- registration (W1) --------------------------------------------------------------------------


def register(c: RequestContext, *, email: str, full_name: str, workspace_name: str, password: str) -> None:
    """Same work for new and existing addresses: one Argon2 hash, one row, one job. The job decides
    between the verify link and the "already registered" notice (S2: no lookup on the request path)."""
    ratelimit.hit(c.db, f"register:ip:{c.ip}", limit=5, window_seconds=3600)
    passwords.check(c.settings, password, email)
    pending = PendingRegistration(
        id=new_id(),
        email=email,
        password_hash=password_hasher(c.settings).hash(password),
        full_name=full_name,
        workspace_name=workspace_name,
        token_hash=None,  # minted by the job (S2 token decision)
        expires_at=clock.now() + timedelta(hours=c.settings.email_verify_token_hours),
    )
    c.db.add(pending)
    c.db.flush()
    jobs.enqueue(c.db, "identity_registration_email", tenant_id=None, payload={"pending_id": str(pending.id)})


def verify_email(c: RequestContext, token: str, password: str) -> None:
    """Creates user + workspace + admin membership only now (S1 review finding 1). The link alone is not
    enough: the registration password must match, so an attacker's verify email for someone else's
    address cannot be completed by the address owner by mistake (S1 code review finding 8)."""
    ratelimit.hit(c.db, f"verify:ip:{c.ip}", limit=10, window_seconds=3600)
    pending = c.db.scalar(
        select(PendingRegistration)
        .where(PendingRegistration.token_hash == hash_token(token))
        .with_for_update()
    )
    now = clock.now()
    if pending is None or pending.used_at is not None or pending.expires_at <= now:
        raise LinkExpired()
    if not password_hasher(c.settings).verify(pending.password_hash, password).ok:
        raise LinkExpired()  # same answer as a bad link; the pending row stays usable
    c.db.execute(
        update(PendingRegistration)
        .where(PendingRegistration.email == pending.email, PendingRegistration.used_at.is_(None))
        .values(used_at=now)
    )
    if _user_by_email(c.db, pending.email, lock=True) is not None:
        raise LinkExpired()  # someone verified this address first; nothing is revealed beyond "invalid"
    user = User(
        id=new_id(),
        email=pending.email,
        password_hash=pending.password_hash,
        full_name=pending.full_name,
        email_verified_at=now,
        password_changed_at=now,
    )
    c.db.add(user)
    c.db.flush()
    tenancy.create_workspace(c.db, name=pending.workspace_name, admin_user_id=user.id, meta=c.audit_meta())


# ---- login, MFA, logout -------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginResult:
    session: sessions.NewSession
    mfa_required: bool


def _choose_tenant(db: Session, user_id: UUID) -> UUID | None:
    active = tenancy.active_tenant_ids(db, user_id)
    last = sessions.last_tenant(db, user_id)
    if last in active:
        return last
    return active[0] if active else None


def _fail_known(c: RequestContext, user: User, *, mfa_stage: bool) -> None:
    locked = lockout.user_failure(c.settings, user)
    audit_user_event(c, user.id, Action.AUTH_MFA_FAILED if mfa_stage else Action.AUTH_LOGIN_FAILED)
    if locked:
        audit_user_event(c, user.id, Action.AUTH_LOCKED, actor=Actor("system"))
        raise AccountLocked()
    raise InvalidMfaCode() if mfa_stage else InvalidCredentials()


def login(c: RequestContext, *, email: str, password: str, trusted_token: str | None = None) -> LoginResult:
    ratelimit.hit(c.db, f"login:ip:{c.ip}", limit=c.settings.login_ip_limit_per_minute, window_seconds=60)
    ratelimit.hit(
        c.db, f"login:{_hkey(c, 'login', f'{c.ip}|{email_key(email)}')}", limit=5, window_seconds=60
    )
    user = _user_by_email(c.db, email, lock=True)
    result = password_hasher(c.settings).verify(user.password_hash if user else None, password)
    if user is None or user.status != "active":
        state = lockout.unknown_state(c.db, c.settings, email)
        if lockout.is_locked(state):
            raise AccountLocked()
        locked = lockout.unknown_failure(c.db, c.settings, email)
        # No audit row (no tenant); the raw email is never logged, only a keyed hash (S1 D-7).
        log.info(
            "auth.login_failed_unknown", email_hash=_hkey(c, "log", email_key(email))[:16], locked=locked
        )
        raise AccountLocked() if locked else InvalidCredentials()
    if lockout.is_locked(lockout.user_state(user)):
        raise AccountLocked()
    if not result.ok:
        _fail_known(c, user, mfa_stage=False)
    if result.needs_rehash:
        user.password_hash = password_hasher(c.settings).hash(password)
    tenant_id = _choose_tenant(c.db, user.id)
    # A trusted device skips the 2FA prompt but gets no step-up: sensitive actions still need password + code.
    skip_mfa = user.mfa_enabled and trusted.is_trusted(c.db, trusted_token, user.id, tenant_id)
    pending = user.mfa_enabled and not skip_mfa
    new = sessions.create(
        c.db,
        c.settings,
        user_id=user.id,
        tenant_id=tenant_id,
        mfa_verified=skip_mfa,
        pending_mfa=pending,
        ip=c.ip,
        user_agent=c.user_agent,
        reauthenticated=not user.mfa_enabled,
    )
    if not pending:
        _complete_login(c, user)
    return LoginResult(session=new, mfa_required=pending)


def _complete_login(c: RequestContext, user: User) -> None:
    """Full authentication reached: only now are failures reset (S1 review finding 4)."""
    lockout.user_success(user)
    user.last_login_at = clock.now()
    audit_user_event(c, user.id, Action.AUTH_LOGIN_SUCCEEDED)


def verify_mfa(c: RequestContext, principal: Principal, code: str) -> sessions.NewSession:
    if not principal.mfa_pending:
        raise InvalidMfaCode()
    user = _user(c.db, principal.user_id, lock=True)
    if lockout.is_locked(lockout.user_state(user)):
        sessions.revoke(c.db, principal.session_id)
        raise AccountLocked()
    kind = mfa.check_code(c.db, secret_box(c.settings), user, code)
    if kind is None:
        row = c.db.get(AuthSession, principal.session_id, with_for_update=True)
        if row is not None:
            row.mfa_failed_count += 1
            if row.mfa_failed_count >= c.settings.mfa_max_attempts_per_session:
                row.revoked_at = clock.now()
        _fail_known(c, user, mfa_stage=True)
    sessions.revoke(c.db, principal.session_id)  # rotation on MFA success
    new = sessions.create(
        c.db,
        c.settings,
        user_id=user.id,
        tenant_id=principal.tenant_id,
        mfa_verified=True,
        pending_mfa=False,
        ip=c.ip,
        user_agent=c.user_agent,
        reauthenticated=True,
    )
    if kind == "recovery":
        audit_user_event(c, user.id, Action.AUTH_RECOVERY_CODE_USED)
    _complete_login(c, user)
    return new


def logout(c: RequestContext, principal: Principal) -> None:
    sessions.revoke(c.db, principal.session_id)
    if principal.tenant_id is not None:
        audit.record(
            c.db,
            tenant_id=principal.tenant_id,
            actor=Actor("user", principal.user_id),
            action=Action.AUTH_LOGOUT,
            entity_type="session",
            meta=c.audit_meta(),
        )


def switch_tenant(c: RequestContext, principal: Principal, tenant_id: UUID) -> sessions.NewSession:
    if tenant_id not in tenancy.active_tenant_ids(c.db, principal.user_id):
        raise NotFound()  # not a member: indistinguishable from a non-existent workspace
    sessions.revoke(c.db, principal.session_id)
    new = sessions.create(
        c.db,
        c.settings,
        user_id=principal.user_id,
        tenant_id=tenant_id,
        mfa_verified=not principal.mfa_pending,
        pending_mfa=False,
        ip=c.ip,
        user_agent=c.user_agent,
    )
    with tenant_scope(c.db, tenant_id):
        audit.record(
            c.db,
            tenant_id=tenant_id,
            actor=Actor("user", principal.user_id),
            action=Action.TENANT_SWITCHED,
            entity_type="tenant",
            entity_id=tenant_id,
            meta=c.audit_meta(),
        )
    return new


# ---- MFA enrolment ------------------------------------------------------------------------------


def mfa_setup(c: RequestContext, principal: Principal) -> tuple[str, str]:
    user = _user(c.db, principal.user_id, lock=True)
    if user.mfa_enabled:
        raise ValidationFailed("Two-factor authentication is already on. Regenerate recovery codes instead.")
    secret = mfa.new_secret()
    user.mfa_secret_enc = mfa.encrypt_secret(secret_box(c.settings), user.id, secret)
    user.mfa_last_used_step = None
    return mfa.provisioning(secret, user.email)


def mfa_enable(c: RequestContext, principal: Principal, code: str) -> tuple[sessions.NewSession, list[str]]:
    user = _user(c.db, principal.user_id, lock=True)
    if user.mfa_enabled or user.mfa_secret_enc is None:
        raise ValidationFailed("Start two-factor setup first.")
    step = mfa.verify_totp(mfa.decrypt_secret(secret_box(c.settings), user), code, user.mfa_last_used_step)
    if step is None:
        raise InvalidMfaCode()
    user.mfa_enabled, user.mfa_last_used_step = True, step
    codes = mfa.new_recovery_codes(c.db, user.id)
    trusted.revoke_all(c.db, user.id)
    sessions.revoke_all(c.db, user.id)  # includes the current one: rotated below
    new = sessions.create(
        c.db,
        c.settings,
        user_id=user.id,
        tenant_id=principal.tenant_id,
        mfa_verified=True,
        pending_mfa=False,
        ip=c.ip,
        user_agent=c.user_agent,
        reauthenticated=False,  # enrolling is not a step-up (S1 code review finding 2)
    )
    audit_user_event(c, user.id, Action.AUTH_MFA_ENABLED, after={"mfa_enabled": True})
    return new, codes


def regenerate_recovery_codes(c: RequestContext, principal: Principal) -> list[str]:
    user = _user(c.db, principal.user_id, lock=True)
    if not user.mfa_enabled:
        raise MfaEnrollmentRequired()
    codes = mfa.new_recovery_codes(c.db, user.id)
    audit_user_event(c, user.id, Action.AUTH_RECOVERY_CODES_REGENERATED)
    return codes


# ---- step-up, password and email changes --------------------------------------------------------


def _check_password(c: RequestContext, user: User, password: str) -> None:
    if lockout.is_locked(lockout.user_state(user)):
        raise AccountLocked()
    if not password_hasher(c.settings).verify(user.password_hash, password).ok:
        _fail_known(c, user, mfa_stage=False)


def _check_password_and_mfa(c: RequestContext, user: User, password: str, mfa_code: str | None) -> None:
    _check_password(c, user, password)
    if user.mfa_enabled and (
        mfa_code is None or mfa.check_code(c.db, secret_box(c.settings), user, mfa_code) is None
    ):
        _fail_known(c, user, mfa_stage=True)


def reauthenticate(c: RequestContext, principal: Principal, password: str, mfa_code: str | None) -> None:
    user = _user(c.db, principal.user_id, lock=True)
    _check_password_and_mfa(c, user, password, mfa_code)
    row = c.db.get(AuthSession, principal.session_id, with_for_update=True)
    if row is None:
        raise Unauthenticated()
    row.reauthenticated_at = clock.now()
    lockout.user_success(user)
    audit_user_event(c, user.id, Action.AUTH_REAUTHENTICATED)


def change_password(c: RequestContext, principal: Principal, current: str, new_password: str) -> None:
    """The route requires a recent step-up (password + MFA); the current password is checked again here."""
    user = _user(c.db, principal.user_id, lock=True)
    _check_password(c, user, current)
    passwords.check(c.settings, new_password, user.email, field="new_password")
    _set_password(c, user, new_password)
    sessions.revoke_all(c.db, user.id, except_id=principal.session_id)
    audit_user_event(c, user.id, Action.AUTH_PASSWORD_CHANGED)


def _set_password(c: RequestContext, user: User, new_password: str) -> None:
    now = clock.now()
    user.password_hash = password_hasher(c.settings).hash(new_password)
    user.password_changed_at, user.updated_at = now, now
    trusted.revoke_all(c.db, user.id)
    c.db.execute(
        update(UserToken)
        # every open token (reset, email change): a password reset must also stop a pending email change
        .where(UserToken.user_id == user.id, UserToken.used_at.is_(None))
        .values(used_at=now)
    )


def forgot_password(c: RequestContext, email: str) -> None:
    """Identical request-path work for every address: two rate-limit hits, one row, one job. The job
    looks the address up (S1 timing finding; S2 moved it from the temporary pool to the queue)."""
    ratelimit.hit(c.db, f"forgot:ip:{c.ip}", limit=5, window_seconds=3600)
    ratelimit.hit(c.db, f"forgot:acct:{_hkey(c, 'forgot', email_key(email))}", limit=3, window_seconds=3600)
    request = PasswordResetRequest(id=new_id(), email=email)
    c.db.add(request)
    c.db.flush()
    jobs.enqueue(c.db, "identity_password_reset", tenant_id=None, payload={"request_id": str(request.id)})


def _consume_token(c: RequestContext, token: str, purpose: str) -> UserToken:
    row = c.db.scalar(
        select(UserToken)
        .where(UserToken.token_hash == hash_token(token), UserToken.purpose == purpose)
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= clock.now():
        raise LinkExpired()
    row.used_at = clock.now()
    return row


def reset_password(c: RequestContext, token: str, new_password: str) -> None:
    """Does not sign in; clears lockout; revokes every session; keeps MFA (review finding 25)."""
    ratelimit.hit(c.db, f"reset:ip:{c.ip}", limit=10, window_seconds=3600)
    row = _consume_token(c, token, "password_reset")
    user = _user(c.db, row.user_id, lock=True)
    if user.status != "active":
        raise LinkExpired()
    passwords.check(c.settings, new_password, user.email, field="new_password")
    _set_password(c, user, new_password)
    lockout.user_clear(user)
    sessions.revoke_all(c.db, user.id)
    audit_user_event(c, user.id, Action.AUTH_PASSWORD_RESET)


def request_email_change(
    c: RequestContext, principal: Principal, *, new_email: str, password: str, mfa_code: str
) -> None:
    """Owner condition D-5: password **and** a current MFA code in this request."""
    user = _user(c.db, principal.user_id, lock=True)
    if not user.mfa_enabled:
        raise MfaEnrollmentRequired()
    if email_key(new_email) == email_key(user.email):
        raise ValidationFailed(
            details=[{"field": "new_email", "code": "unchanged", "message": "This is your current email."}]
        )
    _check_password_and_mfa(c, user, password, mfa_code)
    now = clock.now()
    c.db.execute(
        update(UserToken)
        .where(UserToken.user_id == user.id, UserToken.purpose == "email_change", UserToken.used_at.is_(None))
        .values(used_at=now)
    )
    row = UserToken(
        id=new_id(),
        user_id=user.id,
        purpose="email_change",
        token_hash=None,  # minted by the job
        new_email=new_email,
        expires_at=now + timedelta(hours=c.settings.email_verify_token_hours),
    )
    c.db.add(row)
    c.db.flush()
    jobs.enqueue(
        c.db, "identity_email_change", tenant_id=None, payload={"token_id": str(row.id)}, created_by=user.id
    )
    audit_user_event(c, user.id, Action.AUTH_EMAIL_CHANGE_REQUESTED)


def confirm_email_change(c: RequestContext, token: str) -> None:
    ratelimit.hit(c.db, f"emailconfirm:ip:{c.ip}", limit=10, window_seconds=3600)
    row = _consume_token(c, token, "email_change")
    user = _user(c.db, row.user_id, lock=True)
    if row.new_email is None or user.status != "active" or _user_by_email(c.db, row.new_email) is not None:
        raise LinkExpired()
    before = {"email": user.email}
    user.email, user.updated_at = row.new_email, clock.now()
    sessions.revoke_all(c.db, user.id)
    audit_user_event(c, user.id, Action.AUTH_EMAIL_CHANGED, before=before, after={"email": user.email})


# ---- invitations (W2) ---------------------------------------------------------------------------


def accept_invitation(
    c: RequestContext, principal: Principal | None, *, token: str, full_name: str | None, password: str | None
) -> None:
    ratelimit.hit(c.db, f"accept:ip:{c.ip}", limit=10, window_seconds=3600)
    inv = tenancy.open_invitation(c.db, token)
    user = _user_by_email(c.db, inv.email, lock=True)
    hasher = password_hasher(c.settings)
    if user is not None:
        hasher.verify(None, password or "")  # same Argon2 cost as the new-user branch
        if principal is None or principal.user_id != user.id:
            raise Unauthenticated("Sign in with the invited email address, then open the link again.")
    else:
        if not full_name or not password:
            raise ValidationFailed(
                details=[{"field": "password", "code": "required", "message": "Choose a name and password."}]
            )
        passwords.check(c.settings, password, inv.email)
        now = clock.now()
        user = User(
            id=new_id(),
            email=inv.email,
            password_hash=hasher.hash(password),
            full_name=full_name,
            email_verified_at=now,
            password_changed_at=now,  # the emailed token proves the address
        )
        c.db.add(user)
        c.db.flush()
    tenancy.accept_invitation(c.db, inv, user_id=user.id, meta=c.audit_meta())


# ---- users of the active workspace (A) ----------------------------------------------------------


@dataclass(frozen=True)
class MemberView:
    user_id: UUID
    email: str
    full_name: str
    role: str
    status: str
    mfa_enabled: bool


def list_members(c: RequestContext, tenant_id: UUID) -> list[MemberView]:
    members = tenancy.list_memberships(c.db, tenant_id)
    users = {u.id: u for u in c.db.scalars(select(User).where(User.id.in_([m.user_id for m in members])))}
    return [
        MemberView(
            m.user_id,
            users[m.user_id].email,
            users[m.user_id].full_name,
            m.role,
            m.status,
            users[m.user_id].mfa_enabled,
        )
        for m in members
        if m.user_id in users
    ]


def change_member(
    c: RequestContext,
    principal: Principal,
    tenant_id: UUID,
    user_id: UUID,
    *,
    role: str | None,
    status: str | None,
) -> MemberView:
    _, deactivated = tenancy.change_membership(
        c.db,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        user_id=user_id,
        role=role,
        status=status,
        meta=c.audit_meta(),
    )
    if deactivated:
        sessions.revoke_in_tenant(c.db, user_id, tenant_id)  # FR-ID-05: their sessions here end now
    view = next((m for m in list_members(c, tenant_id) if m.user_id == user_id), None)
    if view is None:
        raise NotFound()
    return view


def actor_labels(db: Session, user_ids: set[UUID]) -> dict[UUID, tuple[str, str]]:
    """(full_name, email) for audit display. Callers pass ids taken from their own tenant's audit rows."""
    if not user_ids:
        return {}
    rows = db.execute(select(User.id, User.full_name, User.email).where(User.id.in_(user_ids)))
    return {r.id: (r.full_name, r.email) for r in rows}


def admin_emails(db: Session, tenant_id: UUID) -> list[str]:
    """Addresses of the workspace's active admins (security notifications)."""
    admins = [
        m.user_id
        for m in tenancy.list_memberships(db, tenant_id)
        if m.role == "admin" and m.status == "active"
    ]
    if not admins:
        return []
    return list(db.scalars(select(User.email).where(User.id.in_(admins), User.status == "active")))
