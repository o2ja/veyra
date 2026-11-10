"""Agreement drafts, approval (four-eyes, split-supersede), cancellation, versions and diffs (FR-AG-02..10).

One pipeline (ADR-011): every version starts as an ``agreement_draft``; the same validator runs on save,
submit and again at approval; approval creates the immutable version(s) in the request transaction
(DATABASE_SCHEMA.md §8). Concurrent transitions are serialised by ``SELECT … FOR UPDATE`` on the draft and
on the agreement (two parallel approvals of one draft: the second sees ``approved`` and gets 409).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import (
    Conflict,
    DomainError,
    FourEyesViolation,
    InvalidState,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from rebate.core.ids import new_id
from rebate.core.jobs import enqueue
from rebate.modules import audit, files, masterdata, tenancy
from rebate.modules.agreements import validator
from rebate.modules.agreements.models import (
    Agreement,
    AgreementDraft,
    AgreementDraftRevision,
    AgreementVersion,
    AgreementVersionCustomer,
    AgreementVersionItem,
    AgreementVersionTier,
)
from rebate.modules.audit import Action, Actor
from rebate.modules.masterdata import Who, keyset

MAX_PAYLOAD_BYTES = 5 * 1024 * 1024  # API_SPECIFICATION.md §1: draft payloads <= 5 MB
SWEEP_JOB = "agreements_expiry_sweep"
ITEM_MONEY = ("reference_cost", "deviated_cost", "sell_price", "flat_amount", "rate", "max_quantity")


class PayloadTooLarge(DomainError):
    code = "payload_too_large"
    status_code = 413
    message = "The agreement payload is larger than 5 MB."


class AiNotEnabled(Conflict):
    code = "ai_not_enabled"
    message = (
        "AI extraction is not available yet: the AI gateway arrives with a later release stage and is opt-in "
        "per workspace. Create the draft manually or from a spreadsheet."
    )


class VersionOverlap(Conflict):
    code = "version_overlap"
    message = "The version overlaps another version of this agreement."


class AuditedFourEyesViolation(FourEyesViolation):
    commit = True  # the audit entry of the refused approval must survive the error response


# ---- helpers --------------------------------------------------------------------------------------------


def business_today(db: Session, tenant_id: UUID) -> date:
    tz = tenancy.get_settings(db, tenant_id).business_timezone
    return clock.now().astimezone(ZoneInfo(tz)).date()


def audit_event(
    db: Session, who: Who, action: Action, entity: str, entity_id: UUID, *, system: bool = False, **kw: Any
) -> None:
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=Actor("system") if system else Actor("user", who.user_id),
        action=action,
        entity_type=entity,
        entity_id=entity_id,
        meta=who.meta,
        **kw,
    )


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == "23P01":
            raise VersionOverlap() from None
        if state == "23505":
            raise Conflict("An agreement with this number already exists for the manufacturer.") from None
        raise


def _draft(db: Session, tenant_id: UUID, draft_id: UUID, *, lock: bool = False) -> AgreementDraft:
    stmt = select(AgreementDraft).where(AgreementDraft.tenant_id == tenant_id, AgreementDraft.id == draft_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def _agreement(db: Session, tenant_id: UUID, agreement_id: UUID, *, lock: bool = False) -> Agreement:
    stmt = select(Agreement).where(Agreement.tenant_id == tenant_id, Agreement.id == agreement_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def get_version(db: Session, tenant_id: UUID, version_id: UUID, *, lock: bool = False) -> AgreementVersion:
    stmt = select(AgreementVersion).where(
        AgreementVersion.tenant_id == tenant_id, AgreementVersion.id == version_id
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def _check_size(payload: dict[str, Any]) -> None:
    if len(json.dumps(payload).encode()) > MAX_PAYLOAD_BYTES:
        raise PayloadTooLarge()


def _schema_version(payload: dict[str, Any]) -> int:
    v = payload.get("payload_schema_version")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else 1


def _run_validator(db: Session, draft: AgreementDraft) -> validator.Result:
    result = validator.validate(
        db,
        draft.tenant_id,
        validator.Draftish(draft.agreement_id, draft.amends_version_id),
        draft.payload,
        business_today(db, draft.tenant_id),
    )
    draft.validation = result.as_json(draft.revision)
    return result


def _draft_audit(d: AgreementDraft, **extra: Any) -> dict[str, Any]:
    return {
        "source": d.source,
        "status": d.status,
        "revision": d.revision,
        "agreement_id": d.agreement_id,
        "amends_version_id": d.amends_version_id,
        **extra,
    }


def version_audit(v: AgreementVersion) -> dict[str, Any]:
    return {
        "agreement_id": v.agreement_id,
        "version_number": v.version_number,
        "status": v.status,
        "valid_from": v.valid_from,
        "valid_to": v.valid_to,
        "content_hash": v.content_hash,
        "draft_id": v.draft_id,
        "derived_from_version_id": v.derived_from_version_id,
        "supersedes_version_id": v.supersedes_version_id,
        "superseded_by_version_id": v.superseded_by_version_id,
    }


# ---- drafts ---------------------------------------------------------------------------------------------


def create_draft(
    db: Session,
    who: Who,
    *,
    source: str,
    agreement_id: UUID | None,
    amends_version_id: UUID | None,
    payload: dict[str, Any] | None,
    source_file_id: UUID | None,
) -> AgreementDraft:
    if source == "ai_extraction":
        raise AiNotEnabled()
    if source == "spreadsheet_template" and source_file_id is None:
        raise ValidationFailed(
            details=[{"field": "source_file_id", "code": "required", "message": "Attach the spreadsheet."}]
        )
    if amends_version_id is not None:
        amended = get_version(db, who.tenant_id, amends_version_id)
        if agreement_id is not None and agreement_id != amended.agreement_id:
            raise ValidationFailed(
                details=[
                    {
                        "field": "amends_version_id",
                        "code": "mismatch",
                        "message": "Not a version of agreement_id.",
                    }
                ]
            )
        agreement_id = amended.agreement_id
        if payload is None:
            payload = version_payload(db, amended)
    elif agreement_id is not None:
        _agreement(db, who.tenant_id, agreement_id)
    if source_file_id is not None:
        files.get(db, who.tenant_id, source_file_id)
    payload = payload if payload is not None else {"payload_schema_version": 1}
    _check_size(payload)
    now = clock.now()
    draft = AgreementDraft(
        tenant_id=who.tenant_id,
        id=new_id(),
        agreement_id=agreement_id,
        amends_version_id=amends_version_id,
        source=source,
        status="draft",
        payload=payload,
        payload_schema_version=_schema_version(payload),
        revision=1,
        source_file_id=source_file_id,
        created_by=who.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(draft)
    db.flush()
    db.add(_revision_row(draft, who, "Created"))
    _run_validator(db, draft)
    db.flush()
    audit_event(
        db,
        who,
        Action.AGREEMENT_DRAFT_CREATED,
        "agreement_draft",
        draft.id,
        after=_draft_audit(draft, source_file_id=source_file_id),
    )
    return draft


def _revision_row(draft: AgreementDraft, who: Who, summary: str | None) -> AgreementDraftRevision:
    return AgreementDraftRevision(
        tenant_id=draft.tenant_id,
        id=new_id(),
        draft_id=draft.id,
        revision=draft.revision,
        payload=draft.payload,
        edited_by=who.user_id,
        change_summary=summary,
        created_at=clock.now(),
    )


def _editable(draft: AgreementDraft, revision: int) -> None:
    if draft.status not in ("draft", "needs_manual_entry"):
        raise InvalidState("Only drafts in status draft can be edited.")
    if draft.revision != revision:
        raise PreconditionFailed()


def revise(
    db: Session,
    who: Who,
    draft: AgreementDraft,
    payload: dict[str, Any],
    summary: str | None,
    action: Action,
    **audit_extra: Any,
) -> AgreementDraft:
    """New revision of a locked, editable draft: payload replaced, revision row, validation, audit."""
    _check_size(payload)
    before = _draft_audit(draft)
    draft.payload = payload
    draft.payload_schema_version = _schema_version(payload)
    draft.revision += 1
    draft.status = "draft"
    draft.updated_at = clock.now()
    db.add(_revision_row(draft, who, summary))
    result = _run_validator(db, draft)
    db.flush()
    audit_event(
        db,
        who,
        action,
        "agreement_draft",
        draft.id,
        before=before,
        after=_draft_audit(draft, change_summary=summary, error_count=len(result.errors), **audit_extra),
    )
    return draft


def update_draft(
    db: Session, who: Who, draft_id: UUID, revision: int, payload: dict[str, Any], summary: str | None
) -> AgreementDraft:
    draft = _draft(db, who.tenant_id, draft_id, lock=True)
    _editable(draft, revision)
    return revise(db, who, draft, payload, summary, Action.AGREEMENT_DRAFT_UPDATED)


def editable_draft(db: Session, who: Who, draft_id: UUID, revision: int) -> AgreementDraft:
    draft = _draft(db, who.tenant_id, draft_id, lock=True)
    _editable(draft, revision)
    return draft


def get_draft(db: Session, tenant_id: UUID, draft_id: UUID) -> AgreementDraft:
    return _draft(db, tenant_id, draft_id)


def list_drafts(
    db: Session,
    tenant_id: UUID,
    *,
    status: list[str] | None,
    agreement_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> list[AgreementDraft]:
    stmt: Select[Any] = select(AgreementDraft).where(AgreementDraft.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(AgreementDraft.status.in_(status))
    if agreement_id is not None:
        stmt = stmt.where(AgreementDraft.agreement_id == agreement_id)
    return list(db.scalars(keyset(stmt, AgreementDraft.id, cursor, limit)))


def validate_draft(db: Session, who: Who, draft_id: UUID) -> AgreementDraft:
    draft = _draft(db, who.tenant_id, draft_id, lock=True)
    result = _run_validator(db, draft)
    db.flush()
    audit_event(
        db,
        who,
        Action.AGREEMENT_DRAFT_VALIDATED,
        "agreement_draft",
        draft.id,
        after=_draft_audit(draft, error_count=len(result.errors), warning_count=len(result.warnings)),
    )
    return draft


def _blocking(result: validator.Result, message: str) -> ValidationFailed:
    return ValidationFailed(message, details=result.errors[: validator.MAX_REPORTED_ERRORS])


def submit(db: Session, who: Who, draft_id: UUID, acknowledged: list[str]) -> AgreementDraft:
    draft = _draft(db, who.tenant_id, draft_id, lock=True)
    if draft.status != "draft":
        raise InvalidState("Only drafts in status draft can be submitted.")
    result = _run_validator(db, draft)
    if not result.valid:
        raise _blocking(result, "Draft cannot be submitted.")
    missing = [w for w in result.warnings if w["id"] not in set(acknowledged)]
    if missing:
        raise ValidationFailed(
            "Acknowledge the flagged fields before submitting.",
            details=[
                {
                    "field": w["field"],
                    "code": "unacknowledged_warning",
                    "message": f"{w['message']} ({w['id']})",
                }
                for w in missing
            ],
        )
    before = _draft_audit(draft)
    now = clock.now()
    draft.status, draft.submitted_by, draft.submitted_at, draft.updated_at = (
        "pending_approval",
        who.user_id,
        now,
        now,
    )
    draft.review_comment = None
    db.flush()
    audit_event(
        db,
        who,
        Action.AGREEMENT_DRAFT_SUBMITTED,
        "agreement_draft",
        draft.id,
        before=before,
        after=_draft_audit(draft, acknowledged_warnings=sorted(w["id"] for w in result.warnings)),
    )
    return draft


def _review(db: Session, who: Who, draft_id: UUID) -> tuple[AgreementDraft, dict[str, Any]]:
    draft = _draft(db, who.tenant_id, draft_id, lock=True)
    if draft.status != "pending_approval":
        raise InvalidState("Only drafts pending approval can be reviewed.")
    return draft, _draft_audit(draft)


def return_draft(db: Session, who: Who, draft_id: UUID, comment: str) -> AgreementDraft:
    draft, before = _review(db, who, draft_id)
    now = clock.now()
    draft.status, draft.review_comment, draft.reviewed_by, draft.reviewed_at = (
        "draft",
        comment,
        who.user_id,
        now,
    )
    draft.updated_at = now
    db.flush()
    audit_event(
        db,
        who,
        Action.AGREEMENT_DRAFT_RETURNED,
        "agreement_draft",
        draft.id,
        before=before,
        after=_draft_audit(draft, review_comment=comment),
    )
    return draft


def reject(db: Session, who: Who, draft_id: UUID, reason: str) -> AgreementDraft:
    draft, before = _review(db, who, draft_id)
    now = clock.now()
    draft.status, draft.rejection_reason, draft.reviewed_by, draft.reviewed_at = (
        "rejected",
        reason,
        who.user_id,
        now,
    )
    draft.updated_at = now
    db.flush()
    audit_event(
        db,
        who,
        Action.AGREEMENT_DRAFT_REJECTED,
        "agreement_draft",
        draft.id,
        before=before,
        after=_draft_audit(draft, rejection_reason=reason),
    )
    return draft


def makers(db: Session, draft: AgreementDraft) -> set[UUID]:
    """Everyone who created or edited the draft (SECURITY.md §3 four-eyes; stricter than 'last editor')."""
    editors = db.scalars(
        select(AgreementDraftRevision.edited_by).where(
            AgreementDraftRevision.tenant_id == draft.tenant_id, AgreementDraftRevision.draft_id == draft.id
        )
    )
    return {draft.created_by, *editors}


# ---- approval and split-supersede -------------------------------------------------------------------------


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _canonical(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def content_hash(
    agreement_type: str,
    priority: int,
    rules: dict[str, Any],
    items: list[dict[str, Any]],
    customers: list[tuple[UUID, str]],
    tiers: list[dict[str, Any]],
) -> str:
    """SHA-256 of the canonical terms (validity excluded, so split copies share the hash of their source)."""
    terms = {
        "agreement_type": agreement_type,
        "priority": priority,
        "rules": rules,
        "items": sorted(
            (
                {
                    "product_id": str(i["product_id"]),
                    "customer_id": str(i.get("customer_id") or ""),
                    "claim_uom": i.get("claim_uom"),
                    **{k: _canonical(i.get(k)) for k in ITEM_MONEY},
                }
                for i in items
            ),
            key=lambda i: (i["product_id"], i["customer_id"]),
        ),
        "customers": sorted([str(c), s] for c, s in customers),
        "tiers": sorted(
            (
                {
                    "tier_number": t["tier_number"],
                    "lower_bound": _canonical(t["lower_bound"]),
                    "rate": _canonical(t.get("rate")),
                }
                for t in tiers
            ),
            key=lambda t: t["tier_number"],
        ),
    }
    return hashlib.sha256(json.dumps(terms, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class Terms:
    items: list[dict[str, Any]]
    customers: list[tuple[UUID, str]]
    tiers: list[dict[str, Any]]


def _terms_of(version: AgreementVersion, db: Session) -> Terms:
    items = db.scalars(
        select(AgreementVersionItem)
        .where(
            AgreementVersionItem.tenant_id == version.tenant_id, AgreementVersionItem.version_id == version.id
        )
        .order_by(AgreementVersionItem.line_number)
    ).all()
    customers = db.execute(
        select(AgreementVersionCustomer.customer_id, AgreementVersionCustomer.scope).where(
            AgreementVersionCustomer.tenant_id == version.tenant_id,
            AgreementVersionCustomer.version_id == version.id,
        )
    ).all()
    tiers = db.scalars(
        select(AgreementVersionTier)
        .where(
            AgreementVersionTier.tenant_id == version.tenant_id, AgreementVersionTier.version_id == version.id
        )
        .order_by(AgreementVersionTier.tier_number)
    ).all()
    fields = ("line_number", "product_id", "customer_id", "claim_uom", *ITEM_MONEY)
    return Terms(
        [{f: getattr(i, f) for f in fields} for i in items],
        [(c, s) for c, s in customers],
        [{"tier_number": t.tier_number, "lower_bound": t.lower_bound, "rate": t.rate} for t in tiers],
    )


def _insert_version(db: Session, version: AgreementVersion, terms: Terms) -> None:
    """Version row and its terms in this transaction (the child trigger refuses any later addition)."""
    db.add(version)
    _flush(db)
    t, v = version.tenant_id, version.id
    if terms.items:
        db.add_all(AgreementVersionItem(tenant_id=t, id=new_id(), version_id=v, **i) for i in terms.items)
    db.add_all(
        AgreementVersionCustomer(tenant_id=t, version_id=v, customer_id=c, scope=s)
        for c, s in terms.customers
    )
    db.add_all(AgreementVersionTier(tenant_id=t, id=new_id(), version_id=v, **x) for x in terms.tiers)
    _flush(db)


def _status_for(valid_to: date, today: date) -> str:
    return "expired" if valid_to < today else "active"


def _next_number(db: Session, agreement: Agreement) -> int:
    current = db.scalar(
        select(func.max(AgreementVersion.version_number)).where(
            AgreementVersion.tenant_id == agreement.tenant_id, AgreementVersion.agreement_id == agreement.id
        )
    )
    return (current or 0) + 1


def _copy(
    src: AgreementVersion, valid_from: date, valid_to: date, number: int, who: Who, today: date
) -> AgreementVersion:
    """A split piece: terms identical to ``src`` (derived_from), a narrower validity."""
    return AgreementVersion(
        tenant_id=src.tenant_id,
        id=new_id(),
        agreement_id=src.agreement_id,
        version_number=number,
        status=_status_for(valid_to, today),
        valid_from=valid_from,
        valid_to=valid_to,
        rules=src.rules,
        rules_schema_version=src.rules_schema_version,
        priority=src.priority,
        source=src.source,
        draft_id=src.draft_id,
        source_file_id=src.source_file_id,
        derived_from_version_id=src.id,
        content_hash=src.content_hash,
        created_by=src.created_by,
        approved_by=who.user_id,
        activated_at=clock.now(),
        created_txid=0,  # replaced by the insert trigger
    )


def _retire(db: Session, who: Who, version: AgreementVersion, status: str) -> dict[str, Any]:
    before = version_audit(version)
    version.status = status
    if status == "superseded":
        version.superseded_at = clock.now()
    _flush(db)
    return before


def approve(db: Session, who: Who, draft_id: UUID) -> AgreementDraft:
    draft = _draft(db, who.tenant_id, draft_id, lock=True)
    if draft.status != "pending_approval":
        raise InvalidState("Only drafts pending approval can be approved.")
    settings = tenancy.get_settings(db, who.tenant_id)
    if settings.four_eyes_required and who.user_id in makers(db, draft):
        audit_event(
            db,
            who,
            Action.AGREEMENT_DRAFT_FOUR_EYES_VIOLATION,
            "agreement_draft",
            draft.id,
            after=_draft_audit(draft),
        )
        raise AuditedFourEyesViolation()
    today = business_today(db, who.tenant_id)
    result = _run_validator(db, draft)
    if not result.valid or result.resolved is None:
        raise _blocking(result, "The draft no longer validates; return it to the analyst.")
    r = result.resolved
    h, now = r.payload.header, clock.now()

    if draft.agreement_id is None:
        agreement = Agreement(
            tenant_id=who.tenant_id,
            id=new_id(),
            manufacturer_id=r.manufacturer_id,
            agreement_type=h.agreement_type,
            external_number=h.external_number,
            name=h.name,
            created_by=draft.created_by,
            created_at=now,
            updated_at=now,
        )
        db.add(agreement)
        _flush(db)
        draft.agreement_id = agreement.id
        audit_event(
            db,
            who,
            Action.AGREEMENT_CREATED,
            "agreement",
            agreement.id,
            after={
                "manufacturer_id": agreement.manufacturer_id,
                "agreement_type": agreement.agreement_type,
                "external_number": agreement.external_number,
                "name": agreement.name,
            },
        )
    else:
        agreement = _agreement(db, who.tenant_id, draft.agreement_id, lock=True)
        if agreement.cancelled_at is not None:
            raise InvalidState("The agreement was cancelled.")
        agreement.name, agreement.updated_at = h.name, now

    valid_from, valid_to = date.fromisoformat(h.valid_from), date.fromisoformat(h.valid_to)
    rules = r.payload.rules.model_dump(mode="json")
    items = [
        {
            "line_number": n,
            "product_id": pid,
            "customer_id": None,
            "claim_uom": i.claim_uom,
            **{k: _dec(getattr(i, k)) for k in ITEM_MONEY},
        }
        for n, (i, pid) in enumerate(zip(r.payload.items, r.product_ids, strict=True), start=1)
    ]
    tiers = [
        {"tier_number": t.tier_number, "lower_bound": Decimal(t.lower_bound), "rate": Decimal(t.rate)}
        for t in r.payload.tiers
    ]
    terms = Terms(items, r.customers, tiers)
    number = _next_number(db, agreement)

    amended: AgreementVersion | None = None
    left: tuple[date, date] | None = None
    right: tuple[date, date] | None = None
    superseded_before: dict[str, Any] = {}
    if draft.amends_version_id is not None:
        amended = get_version(db, who.tenant_id, draft.amends_version_id, lock=True)
        if amended.status not in ("active", "expired"):
            raise InvalidState("The amended version is no longer in force.")
        superseded_before = _retire(db, who, amended, "superseded")
        if amended.valid_from < valid_from:
            left = (amended.valid_from, valid_from - timedelta(days=1))
        if amended.valid_to > valid_to:
            right = (valid_to + timedelta(days=1), amended.valid_to)

    created: list[AgreementVersion] = []
    if amended is not None and left is not None:  # chronological numbering: v1a, v2, (v1b)
        created.append(_copy(amended, *left, number, who, today))
        number += 1
    new_version = AgreementVersion(
        tenant_id=who.tenant_id,
        id=new_id(),
        agreement_id=agreement.id,
        version_number=number,
        status=_status_for(valid_to, today),
        valid_from=valid_from,
        valid_to=valid_to,
        rules=rules,
        rules_schema_version=r.payload.rules.schema_version,
        priority=h.priority,
        source=draft.source,
        draft_id=draft.id,
        source_file_id=draft.source_file_id,
        supersedes_version_id=amended.id if amended is not None else None,
        content_hash=content_hash(h.agreement_type, h.priority, rules, items, r.customers, tiers),
        created_by=draft.created_by,
        approved_by=who.user_id,
        activated_at=now,
        created_txid=0,  # replaced by the insert trigger
    )
    created.append(new_version)
    if amended is not None and right is not None:
        created.append(_copy(amended, *right, number + 1, who, today))

    source_terms = _terms_of(amended, db) if amended is not None else terms
    for v in created:
        _insert_version(db, v, terms if v is new_version else source_terms)
        audit_event(
            db, who, Action.AGREEMENT_VERSION_ACTIVATED, "agreement_version", v.id, after=version_audit(v)
        )
    if amended is not None:
        amended.superseded_by_version_id = new_version.id
        _flush(db)
        audit_event(
            db,
            who,
            Action.AGREEMENT_VERSION_SUPERSEDED,
            "agreement_version",
            amended.id,
            before=superseded_before,
            after=version_audit(amended),
        )

    before = _draft_audit(draft)
    draft.status, draft.reviewed_by, draft.reviewed_at = "approved", who.user_id, now
    draft.resulting_version_id, draft.updated_at = new_version.id, now
    _flush(db)
    audit_event(
        db,
        who,
        Action.AGREEMENT_DRAFT_APPROVED,
        "agreement_draft",
        draft.id,
        before=before,
        after=_draft_audit(draft, resulting_version_id=new_version.id),
    )
    schedule_sweep(db, who.tenant_id, today)
    return draft


def schedule_sweep(db: Session, tenant_id: UUID, day: date, since: date | None = None) -> None:
    """At most one expiry sweep per tenant and business day (dedupe key), first enqueued on approval."""
    enqueue(
        db,
        SWEEP_JOB,
        tenant_id=tenant_id,
        payload={"since": since.isoformat() if since else ""},
        dedupe_key=f"{SWEEP_JOB}:{tenant_id}:{day.isoformat()}",
        run_at=None if since is None else _midnight_utc(db, tenant_id, day),
    )


def _midnight_utc(db: Session, tenant_id: UUID, day: date) -> datetime:
    tz = ZoneInfo(tenancy.get_settings(db, tenant_id).business_timezone)
    return datetime.combine(day, time(0, 5), tz)


# ---- cancellation ---------------------------------------------------------------------------------------


def cancel_agreement(
    db: Session, who: Who, agreement_id: UUID, effective_date: date, reason: str
) -> Agreement:
    """Versions from ``effective_date`` on are cancelled; one spanning it is split (the part before stays)."""
    agreement = _agreement(db, who.tenant_id, agreement_id, lock=True)
    if agreement.cancelled_at is not None:
        raise InvalidState("The agreement is already cancelled.")
    today = business_today(db, who.tenant_id)
    versions = db.scalars(
        select(AgreementVersion)
        .where(
            AgreementVersion.tenant_id == who.tenant_id,
            AgreementVersion.agreement_id == agreement.id,
            AgreementVersion.status.in_(("active", "expired")),
        )
        .order_by(AgreementVersion.valid_from)
        .with_for_update()
    ).all()
    number = _next_number(db, agreement)
    for v in versions:
        if v.valid_to < effective_date:
            continue
        if v.valid_from >= effective_date:
            before = _retire(db, who, v, "cancelled")
            audit_event(
                db,
                who,
                Action.AGREEMENT_VERSION_CANCELLED,
                "agreement_version",
                v.id,
                before=before,
                after=version_audit(v),
            )
            continue
        before = _retire(db, who, v, "superseded")
        piece = _copy(v, v.valid_from, effective_date - timedelta(days=1), number, who, today)
        number += 1
        _insert_version(db, piece, _terms_of(v, db))
        v.superseded_by_version_id = piece.id
        _flush(db)
        audit_event(
            db,
            who,
            Action.AGREEMENT_VERSION_ACTIVATED,
            "agreement_version",
            piece.id,
            after=version_audit(piece),
        )
        audit_event(
            db,
            who,
            Action.AGREEMENT_VERSION_SUPERSEDED,
            "agreement_version",
            v.id,
            before=before,
            after=version_audit(v),
        )
    agreement.cancelled_at = agreement.updated_at = clock.now()
    _flush(db)
    audit_event(
        db,
        who,
        Action.AGREEMENT_CANCELLED,
        "agreement",
        agreement.id,
        after={"cancelled_at": agreement.cancelled_at, "effective_date": effective_date, "reason": reason},
    )
    return agreement


# ---- read side: agreements, versions, items, diff -------------------------------------------------------


def list_agreements(
    db: Session,
    tenant_id: UUID,
    *,
    manufacturer_id: UUID | None,
    agreement_type: str | None,
    limit: int,
    cursor: str | None,
) -> list[Agreement]:
    stmt: Select[Any] = select(Agreement).where(Agreement.tenant_id == tenant_id)
    if manufacturer_id is not None:
        stmt = stmt.where(Agreement.manufacturer_id == manufacturer_id)
    if agreement_type is not None:
        stmt = stmt.where(Agreement.agreement_type == agreement_type)
    return list(db.scalars(keyset(stmt, Agreement.id, cursor, limit)))


def get_agreement(db: Session, tenant_id: UUID, agreement_id: UUID) -> Agreement:
    return _agreement(db, tenant_id, agreement_id)


def current_versions(db: Session, tenant_id: UUID, agreement_ids: list[UUID]) -> dict[UUID, AgreementVersion]:
    today = business_today(db, tenant_id)
    rows = db.scalars(
        select(AgreementVersion).where(
            AgreementVersion.tenant_id == tenant_id,
            AgreementVersion.agreement_id.in_(agreement_ids),
            AgreementVersion.status == "active",
            AgreementVersion.valid_from <= today,
            AgreementVersion.valid_to >= today,
        )
    )
    return {v.agreement_id: v for v in rows}


def list_versions(
    db: Session, tenant_id: UUID, agreement_id: UUID, limit: int, cursor: str | None
) -> list[AgreementVersion]:
    _agreement(db, tenant_id, agreement_id)
    stmt: Select[Any] = select(AgreementVersion).where(
        AgreementVersion.tenant_id == tenant_id, AgreementVersion.agreement_id == agreement_id
    )
    return list(db.scalars(keyset(stmt, AgreementVersion.id, cursor, limit, newest_first=False)))


def version_detail(db: Session, tenant_id: UUID, version_id: UUID) -> dict[str, Any]:
    v = get_version(db, tenant_id, version_id)
    a = _agreement(db, tenant_id, v.agreement_id)
    terms = _terms_of(v, db)
    codes = _customer_codes(db, tenant_id)
    return {
        **{c: getattr(v, c) for c in v.__table__.columns.keys() if c not in ("valid_range", "created_txid")},
        "agreement_type": a.agreement_type,
        "manufacturer_id": a.manufacturer_id,
        "external_number": a.external_number,
        "name": a.name,
        "customers": [
            {"customer_id": c, "customer_code": codes.get(c), "scope": s} for c, s in terms.customers
        ],
        "tiers": terms.tiers,
        "item_count": len(terms.items),
    }


def _customer_codes(db: Session, tenant_id: UUID) -> dict[UUID, str]:
    return {UUID(cid): code for code, cid in masterdata.master_index(db, tenant_id).customers.items()}


def _skus(db: Session, tenant_id: UUID) -> dict[UUID, str]:
    out: dict[UUID, str] = {}
    for sku, (pid, _) in masterdata.master_index(db, tenant_id).products.items():
        out.setdefault(UUID(pid), sku)
    return out


def list_items(
    db: Session, tenant_id: UUID, version_id: UUID, limit: int, cursor: str | None
) -> list[tuple[AgreementVersionItem, str]]:
    get_version(db, tenant_id, version_id)
    stmt: Select[Any] = select(AgreementVersionItem).where(
        AgreementVersionItem.tenant_id == tenant_id, AgreementVersionItem.version_id == version_id
    )
    rows = list(db.scalars(keyset(stmt, AgreementVersionItem.id, cursor, limit, newest_first=False)))
    skus = _skus(db, tenant_id)
    return [(i, skus.get(i.product_id, "")) for i in rows]


def version_payload(db: Session, v: AgreementVersion) -> dict[str, Any]:
    """The payload (BUSINESS_LOGIC.md §3.3 shape) of an approved version: amendment start point and diff input.
    Master-data codes are today's codes for the stored ids; the master index prefers codes over aliases."""
    a = _agreement(db, v.tenant_id, v.agreement_id)
    index = masterdata.master_index(db, v.tenant_id)
    manufacturer = next((c for c, mid in index.manufacturers.items() if mid == str(a.manufacturer_id)), "")
    customers = {UUID(cid): code for code, cid in index.customers.items()}
    skus = _skus(db, v.tenant_id)
    terms = _terms_of(v, db)
    return {
        "payload_schema_version": 1,
        "header": {
            "agreement_type": a.agreement_type,
            "manufacturer_code": manufacturer,
            "external_number": a.external_number,
            "name": a.name,
            "valid_from": v.valid_from.isoformat(),
            "valid_to": v.valid_to.isoformat(),
            "priority": v.priority,
        },
        "rules": v.rules,
        "customers": [{"customer_code": customers.get(c, str(c)), "scope": s} for c, s in terms.customers],
        "items": [
            {
                "sku": skus.get(i["product_id"], str(i["product_id"])),
                "claim_uom": i["claim_uom"],
                **{k: _canonical(i[k]) for k in ITEM_MONEY},
            }
            for i in terms.items
        ],
        "tiers": [
            {
                "tier_number": t["tier_number"],
                "lower_bound": _canonical(t["lower_bound"]),
                "rate": _canonical(t["rate"]),
            }
            for t in terms.tiers
        ],
    }


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else k, v, out)
    else:
        out[prefix] = value


def _diff_view(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _flatten("header", payload["header"], out)
    _flatten("rules", payload["rules"], out)
    for c in payload["customers"]:
        out[f"customers[{c['customer_code']}].scope"] = c["scope"]
    for i in payload["items"]:
        _flatten(f"items[{i['sku']}]", {k: v for k, v in i.items() if k != "sku"}, out)
    for t in payload["tiers"]:
        _flatten(f"tiers[{t['tier_number']}]", {k: v for k, v in t.items() if k != "tier_number"}, out)
    return out


def diff(db: Session, tenant_id: UUID, a_id: UUID, b_id: UUID) -> list[dict[str, Any]]:
    """Field-level differences of the terms of two versions (items keyed by SKU, customers by code, tiers
    by number). A field that exists on one side only has ``None`` on the other."""
    a = _diff_view(version_payload(db, get_version(db, tenant_id, a_id)))
    b = _diff_view(version_payload(db, get_version(db, tenant_id, b_id)))
    return [
        {"path": p, "before": a.get(p), "after": b.get(p)}
        for p in sorted(a.keys() | b.keys())
        if a.get(p) != b.get(p)
    ]
