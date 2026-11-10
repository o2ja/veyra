"""tenancy services: workspaces, settings, memberships, invitations (FR-ID-01/02/04/05/07/08).

All functions run inside the caller's request transaction. Reads are scoped by RLS **and** by an
explicit ``tenant_id`` filter (CLAUDE.md §9). Every state change writes its audit entry here, in the
same transaction.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import available_timezones

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from rebate.core import clock, jobs
from rebate.core.db import set_tenant
from rebate.core.errors import Conflict, InvitationExpired, NotFound, PreconditionFailed, ValidationFailed
from rebate.core.ids import new_id
from rebate.core.security import hash_token
from rebate.modules import audit
from rebate.modules.audit import Action, Actor
from rebate.modules.tenancy.models import Invitation, Membership, Tenant, TenantSetting
from rebate.modules.tenancy.policies import ROLES

SETTINGS_FIELDS = (
    "business_timezone",
    "mfa_required",
    "four_eyes_required",
    "ai_processing_enabled",
    "retention_years",
)


class LastAdmin(Conflict):
    code = "conflict"
    message = "A workspace needs at least one active admin."


@dataclass(frozen=True)
class MembershipRow:
    tenant_id: UUID
    tenant_name: str
    role: str
    status: str


# ---- pre-tenant lookups (SECURITY DEFINER functions, DATABASE_SCHEMA.md §2) ---------------------


def memberships_of(db: Session, user_id: UUID) -> list[MembershipRow]:
    """All workspaces of one user (active tenants only). The caller passes its own user id."""
    rows = db.execute(text("SELECT * FROM fn_user_memberships(:u)"), {"u": user_id}).mappings()
    return [MembershipRow(r["tenant_id"], r["tenant_name"], r["role"], r["status"]) for r in rows]


def active_tenant_ids(db: Session, user_id: UUID) -> list[UUID]:
    return [m.tenant_id for m in memberships_of(db, user_id) if m.status == "active"]


def invitation_tenant(db: Session, token_hash: str) -> tuple[UUID, UUID] | None:
    row = db.execute(text("SELECT * FROM fn_invitation_by_token(:h)"), {"h": token_hash}).one_or_none()
    return None if row is None else (row.tenant_id, row.invitation_id)


# ---- workspaces ---------------------------------------------------------------------------------


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "workspace"
    return f"{base}-{secrets.token_hex(4)}"  # generated, never user-chosen: no uniqueness oracle


def create_workspace(db: Session, *, name: str, admin_user_id: UUID, meta: dict[str, Any]) -> UUID:
    """New tenant + default settings + admin membership + audit (W1), in the caller's transaction.
    Leaves ``app.tenant_id`` set to the new tenant."""
    tenant_id = new_id()
    set_tenant(db, tenant_id)
    tenant = Tenant(id=tenant_id, name=name, slug=_slug(name))
    db.add(tenant)
    db.flush()
    db.add(TenantSetting(tenant_id=tenant_id))
    membership = Membership(tenant_id=tenant_id, id=new_id(), user_id=admin_user_id, role="admin")
    db.add(membership)
    db.flush()
    actor = Actor("user", admin_user_id)
    audit.record(
        db,
        tenant_id=tenant_id,
        actor=actor,
        action=Action.TENANT_CREATED,
        entity_type="tenant",
        entity_id=tenant_id,
        after={"name": name, "slug": tenant.slug, "status": "active"},
        meta=meta,
    )
    audit.record(
        db,
        tenant_id=tenant_id,
        actor=actor,
        action=Action.MEMBERSHIP_CREATED,
        entity_type="membership",
        entity_id=membership.id,
        after={"user_id": admin_user_id, "role": "admin", "status": "active"},
        meta=meta,
    )
    return tenant_id


def tenant_name(db: Session, tenant_id: UUID) -> str:
    name = db.scalar(select(Tenant.name).where(Tenant.id == tenant_id))
    if name is None:
        raise NotFound()
    return name


# ---- settings -----------------------------------------------------------------------------------


def get_settings(db: Session, tenant_id: UUID) -> TenantSetting:
    row = db.scalar(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id))
    if row is None:
        raise NotFound()
    return row


def update_settings(
    db: Session,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    expected_revision: int,
    changes: dict[str, Any],
    meta: dict[str, Any],
) -> TenantSetting:
    row = db.scalar(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id).with_for_update())
    if row is None:
        raise NotFound()
    if row.revision != expected_revision:
        raise PreconditionFailed()
    if "business_timezone" in changes and changes["business_timezone"] not in available_timezones():
        raise ValidationFailed(
            details=[{"field": "business_timezone", "code": "invalid", "message": "Unknown time zone."}]
        )
    before = {f: getattr(row, f) for f in SETTINGS_FIELDS}
    for field, value in changes.items():
        if field not in SETTINGS_FIELDS:
            raise ValueError(field)  # schemas forbid this; defensive
        setattr(row, field, value)
    after = {f: getattr(row, f) for f in SETTINGS_FIELDS}
    if after != before:
        row.revision += 1
        row.updated_at = clock.now()
        audit.record(
            db,
            tenant_id=tenant_id,
            actor=Actor("user", actor_id),
            action=Action.SETTINGS_UPDATED,
            entity_type="settings",
            entity_id=tenant_id,
            before={k: v for k, v in before.items() if after[k] != v},
            after={k: v for k, v in after.items() if before[k] != v},
            meta=meta,
        )
    db.flush()
    return row


# ---- memberships --------------------------------------------------------------------------------


def membership(db: Session, tenant_id: UUID, user_id: UUID, *, for_update: bool = False) -> Membership | None:
    stmt = select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
    return db.scalar(stmt.with_for_update() if for_update else stmt)


def list_memberships(db: Session, tenant_id: UUID) -> list[Membership]:
    return list(
        db.scalars(
            select(Membership).where(Membership.tenant_id == tenant_id).order_by(Membership.created_at)
        )
    )


def _admin_count(db: Session, tenant_id: UUID) -> int:
    rows = db.scalars(
        select(Membership.id)
        .where(Membership.tenant_id == tenant_id, Membership.role == "admin", Membership.status == "active")
        .with_for_update()
    ).all()
    return len(rows)


def change_membership(
    db: Session,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    user_id: UUID,
    role: str | None,
    status: str | None,
    meta: dict[str, Any],
) -> tuple[Membership, bool]:
    """Change role/status of a membership in this tenant only (never the global user; review finding 13).
    Returns (membership, deactivated_now)."""
    row = membership(db, tenant_id, user_id, for_update=True)
    if row is None:
        raise NotFound()
    if role is not None and role not in ROLES:
        raise ValidationFailed()
    before = {"role": row.role, "status": row.status}
    loses_admin = (
        row.role == "admin"
        and row.status == "active"
        and ((role is not None and role != "admin") or status == "disabled")
    )
    if loses_admin and _admin_count(db, tenant_id) <= 1:
        raise LastAdmin()
    if role is not None:
        row.role = role
    if status is not None:
        row.status = status
    after = {"role": row.role, "status": row.status}
    if after == before:
        return row, False
    row.updated_at = clock.now()
    actor = Actor("user", actor_id)
    if before["role"] != after["role"]:
        audit.record(
            db,
            tenant_id=tenant_id,
            actor=actor,
            action=Action.MEMBERSHIP_ROLE_CHANGED,
            entity_type="membership",
            entity_id=row.id,
            before={"user_id": user_id, "role": before["role"]},
            after={"user_id": user_id, "role": after["role"]},
            meta=meta,
        )
    if before["status"] != after["status"]:
        action = (
            Action.MEMBERSHIP_DEACTIVATED if after["status"] == "disabled" else Action.MEMBERSHIP_REACTIVATED
        )
        audit.record(
            db,
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            entity_type="membership",
            entity_id=row.id,
            before={"user_id": user_id, "status": before["status"]},
            after={"user_id": user_id, "status": after["status"]},
            meta=meta,
        )
    db.flush()
    return row, before["status"] == "active" and after["status"] == "disabled"


# ---- invitations --------------------------------------------------------------------------------


def create_invitation(
    db: Session, *, tenant_id: UUID, actor_id: UUID, email: str, role: str, days: int, meta: dict[str, Any]
) -> Invitation:
    """Creates the invitation and enqueues its email; the job mints the token (S2)."""
    now = clock.now()
    db.execute(  # one open invitation per address: older open ones are revoked
        update(Invitation)
        .where(
            Invitation.tenant_id == tenant_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    inv = Invitation(
        tenant_id=tenant_id,
        id=new_id(),
        email=email,
        role=role,
        token_hash=None,
        invited_by=actor_id,
        expires_at=now + timedelta(days=days),
    )
    db.add(inv)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor=Actor("user", actor_id),
        action=Action.INVITATION_CREATED,
        entity_type="invitation",
        entity_id=inv.id,
        after={"email": email, "role": role, "expires_at": inv.expires_at},
        meta=meta,
    )
    jobs.enqueue(
        db,
        "tenancy_invitation_email",
        tenant_id=tenant_id,
        payload={"invitation_id": str(inv.id)},
        created_by=actor_id,
    )
    return inv


def list_invitations(db: Session, tenant_id: UUID) -> list[Invitation]:
    return list(
        db.scalars(
            select(Invitation).where(Invitation.tenant_id == tenant_id).order_by(Invitation.created_at.desc())
        )
    )


def revoke_invitation(
    db: Session, *, tenant_id: UUID, actor_id: UUID, invitation_id: UUID, meta: dict[str, Any]
) -> None:
    inv = db.scalar(
        select(Invitation)
        .where(Invitation.tenant_id == tenant_id, Invitation.id == invitation_id)
        .with_for_update()
    )
    if inv is None:
        raise NotFound()
    if inv.accepted_at is not None or inv.revoked_at is not None:
        return  # idempotent
    inv.revoked_at = clock.now()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor=Actor("user", actor_id),
        action=Action.INVITATION_REVOKED,
        entity_type="invitation",
        entity_id=inv.id,
        before={"email": inv.email, "role": inv.role},
        meta=meta,
    )
    db.flush()


def open_invitation(db: Session, token: str) -> Invitation:
    """Pre-tenant: find the invitation by token, switch the transaction to its tenant, lock it.
    Unknown, expired, used or revoked → one generic 410."""
    found = invitation_tenant(db, hash_token(token))
    if found is None:
        raise InvitationExpired()
    tenant_id, invitation_id = found
    set_tenant(db, tenant_id)
    inv = db.scalar(
        select(Invitation)
        .where(Invitation.tenant_id == tenant_id, Invitation.id == invitation_id)
        .with_for_update()
    )
    if (
        inv is None
        or inv.accepted_at is not None
        or inv.revoked_at is not None
        or inv.expires_at <= clock.now()
    ):
        raise InvitationExpired()
    return inv


def accept_invitation(db: Session, inv: Invitation, *, user_id: UUID, meta: dict[str, Any]) -> None:
    """Membership for ``user_id`` (created, or reactivated with the invited role) + audit."""
    now: datetime = clock.now()
    existing = membership(db, inv.tenant_id, user_id, for_update=True)
    actor = Actor("user", user_id)
    if existing is None:
        m = Membership(
            tenant_id=inv.tenant_id, id=new_id(), user_id=user_id, role=inv.role, invited_by=inv.invited_by
        )
        db.add(m)
        db.flush()
        audit.record(
            db,
            tenant_id=inv.tenant_id,
            actor=actor,
            action=Action.MEMBERSHIP_CREATED,
            entity_type="membership",
            entity_id=m.id,
            after={"user_id": user_id, "role": inv.role, "status": "active"},
            meta=meta,
        )
    elif existing.status != "active":
        existing.status, existing.role, existing.updated_at = "active", inv.role, now
        audit.record(
            db,
            tenant_id=inv.tenant_id,
            actor=actor,
            action=Action.MEMBERSHIP_REACTIVATED,
            entity_type="membership",
            entity_id=existing.id,
            after={"user_id": user_id, "role": inv.role, "status": "active"},
            meta=meta,
        )
    inv.accepted_at, inv.accepted_user_id = now, user_id
    audit.record(
        db,
        tenant_id=inv.tenant_id,
        actor=actor,
        action=Action.INVITATION_ACCEPTED,
        entity_type="invitation",
        entity_id=inv.id,
        after={"email": inv.email, "role": inv.role},
        meta=meta,
    )
    db.flush()
