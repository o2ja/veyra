"""Claim export templates (FR-CB-03): per-manufacturer column layouts. Never deleted; ``active=false`` retires one.
Edits use ``If-Match`` on ``revision`` (API_SPECIFICATION.md §4)."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from rebate.core.ids import new_id
from rebate.modules import audit, masterdata
from rebate.modules.claims import batch_schemas as s
from rebate.modules.claims.models import ClaimExportTemplate
from rebate.modules.masterdata import Who, keyset


class TemplateNameTaken(Conflict):
    message = "This manufacturer already has a template with that name."


def _audit_view(t: ClaimExportTemplate) -> dict[str, Any]:
    return {
        "manufacturer_id": t.manufacturer_id,
        "name": t.name,
        "format": t.format,
        "columns": t.columns,
        "options": t.options,
        "active": t.active,
        "revision": t.revision,
    }


def _columns(body: s.ExportTemplateBody) -> list[dict[str, Any]]:
    return [c.model_dump(exclude_none=True) for c in body.columns]


def get(db: Session, tenant_id: UUID, template_id: UUID) -> ClaimExportTemplate:
    row = db.scalar(
        select(ClaimExportTemplate).where(
            ClaimExportTemplate.tenant_id == tenant_id, ClaimExportTemplate.id == template_id
        )
    )
    if row is None:
        raise NotFound()
    return row


def list_templates(
    db: Session,
    tenant_id: UUID,
    *,
    manufacturer_id: UUID | None,
    active: bool | None,
    limit: int,
    cursor: str | None,
) -> list[ClaimExportTemplate]:
    stmt = select(ClaimExportTemplate).where(ClaimExportTemplate.tenant_id == tenant_id)
    if manufacturer_id is not None:
        stmt = stmt.where(ClaimExportTemplate.manufacturer_id == manufacturer_id)
    if active is not None:
        stmt = stmt.where(ClaimExportTemplate.active == active)
    return list(db.scalars(keyset(stmt, ClaimExportTemplate.id, cursor, limit)))


def create(db: Session, who: Who, body: s.ExportTemplateCreate) -> ClaimExportTemplate:
    if masterdata.existing_manufacturer_ids(db, who.tenant_id, [body.manufacturer_id]) != {
        body.manufacturer_id
    }:
        raise ValidationFailed(
            details=[{"field": "manufacturer_id", "code": "not_found", "message": "Unknown manufacturer."}]
        )
    now = clock.now()
    row = ClaimExportTemplate(
        tenant_id=who.tenant_id,
        id=new_id(),
        manufacturer_id=body.manufacturer_id,
        name=body.name,
        format=body.format,
        columns=_columns(body),
        options=body.options.model_dump(),
        active=body.active,
        revision=1,
        created_by=who.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise TemplateNameTaken() from None
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=audit.Action.CLAIM_TEMPLATE_CREATED,
        entity_type="claim_export_template",
        entity_id=row.id,
        after=_audit_view(row),
        meta=who.meta,
    )
    return row


def replace(
    db: Session, who: Who, template_id: UUID, revision: int, body: s.ExportTemplateBody
) -> ClaimExportTemplate:
    row = db.scalar(
        select(ClaimExportTemplate)
        .where(ClaimExportTemplate.tenant_id == who.tenant_id, ClaimExportTemplate.id == template_id)
        .with_for_update()
    )
    if row is None:
        raise NotFound()
    if row.revision != revision:
        raise PreconditionFailed()
    before = _audit_view(row)
    row.name, row.format, row.columns = body.name, body.format, _columns(body)
    row.options, row.active = body.options.model_dump(), body.active
    row.revision, row.updated_at = row.revision + 1, clock.now()
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise TemplateNameTaken() from None
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=audit.Actor("user", who.user_id),
        action=audit.Action.CLAIM_TEMPLATE_UPDATED,
        entity_type="claim_export_template",
        entity_id=row.id,
        before=before,
        after=_audit_view(row),
        meta=who.meta,
    )
    return row
