"""Extraction orchestration (AI_PROCESSING.md §3-§4, §10): request, the ``extract_agreement`` job body, views.

Who writes what: the API/worker session (``rebate_app``) inserts the *queued* request row and creates the
draft through the shared agreements pipeline (the AgreementValidator needs master-data reads that the AI role
must not have). Every result column of ``document_extractions`` and every ``ai_invocations`` row is written
through ``rebate_ai_writer`` (own short transactions on ``DATABASE_URL_AI``). The draft has no effect on any
calculation until a human approves it.
"""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import Engine, func, insert, select, text, update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.config import Settings
from rebate.core.db import assert_safe_role, make_engine, transaction
from rebate.core.errors import Conflict, InvalidState, NotFound, RateLimited
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext, PermanentError, enqueue
from rebate.modules import agreements, audit, files, ingestion, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.extraction import gateway, ocr, payload, prompts
from rebate.modules.extraction.documents import (
    DEFAULT_LIMITS,
    ExtractionFailure,
    PdfText,
    extract_pdf,
    extract_xlsx,
)
from rebate.modules.extraction.models import AiInvocation, DocumentExtraction
from rebate.modules.extraction.verification import (
    CandidateFormatError,
    Confidence,
    MergedField,
    Status,
    VerificationReport,
    flatten,
    run_verification,
)
from rebate.modules.masterdata import Who

log = structlog.get_logger("rebate.extraction")

JOB = "extract_agreement"
STALE_AFTER = timedelta(hours=1)  # an unfinished extraction older than this is failed (never blocks the file)
JOB_MAX_ATTEMPTS = 3
FINAL = ("completed", "needs_manual_entry", "failed")
ACTIVE = ("queued", "processing")
MAX_PROMPT_CHARS = 400_000  # no chunking in Release 1: a longer document is entered manually
PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NUMERIC = {
    Confidence.high: Decimal("1.000"),
    Confidence.medium: Decimal("0.750"),
    Confidence.low: Decimal("0.500"),
    Confidence.conflict: Decimal("0.250"),
    Confidence.unsupported: Decimal("0.000"),
}


class TooManyExtractions(RateLimited):
    code = "too_many_extractions"
    message = "Too many extractions are already in progress for this workspace. Try again shortly."


class AiNotEnabled(Conflict):
    code = "ai_not_enabled"
    message = (
        "AI processing is not enabled for this workspace (opt-in under settings). "
        "Create the agreement manually or from a spreadsheet."
    )


# ---- AI-role engine -----------------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _engine(url: str) -> Engine:
    engine = make_engine(url)
    assert_safe_role(engine)
    return engine


def ai_engine(settings: Settings) -> Engine:
    if settings.database_url_ai is None:
        raise gateway.NotConfigured("DATABASE_URL_AI is not configured")
    return _engine(settings.database_url_ai.get_secret_value())


def _write(engine: Engine, tenant_id: UUID, extraction_id: UUID, **columns: Any) -> None:
    """The only writer of result columns (rebate_ai_writer; column grants + final-state trigger)."""
    with transaction(engine, tenant_id) as db:
        db.execute(
            update(DocumentExtraction)
            .where(DocumentExtraction.tenant_id == tenant_id, DocumentExtraction.id == extraction_id)
            .values(**columns)
        )


# ---- request and reads --------------------------------------------------------------------------------


@files.register_use_source
def _file_uses(db: Session, tenant_id: UUID, file_id: UUID) -> list[files.Use]:
    rows = db.scalars(
        select(DocumentExtraction)
        .where(DocumentExtraction.tenant_id == tenant_id, DocumentExtraction.file_id == file_id)
        .order_by(DocumentExtraction.id)
    )
    return [files.Use("extraction", x.id, f"{x.kind.capitalize()} extraction ({x.status})") for x in rows]


def request_extraction(
    db: Session, who: Who, file_id: UUID, rerun: bool, settings: Settings
) -> tuple[DocumentExtraction, bool]:
    """Queue an extraction of an agreement file. Returns ``(row, newly_queued)``: an active or (without
    ``rerun``) completed extraction of the same file is reused (idempotent, AI_PROCESSING.md §10)."""
    file = files.get(db, who.tenant_id, file_id)  # 404 for unknown and foreign ids, before anything else
    if not tenancy.get_settings(db, who.tenant_id).ai_processing_enabled:
        raise AiNotEnabled()
    if file.kind != "agreement":
        raise InvalidState("Only files uploaded as kind 'agreement' can be extracted.")
    if file.scan_status != "clean":
        raise InvalidState("The file has not passed the malware scan yet.")
    if file.detected_mime not in (PDF_MIME, XLSX_MIME):
        raise InvalidState("Only PDF and XLSX agreements can be read automatically; enter this one manually.")
    reap_stale(db, who.tenant_id)
    existing = db.scalars(
        select(DocumentExtraction)
        .where(
            DocumentExtraction.tenant_id == who.tenant_id,
            DocumentExtraction.file_id == file_id,
            DocumentExtraction.kind == "agreement",
        )
        .order_by(DocumentExtraction.created_at.desc(), DocumentExtraction.id.desc())
    ).first()
    if existing is not None and (
        existing.status in ("queued", "processing") or (not rerun and existing.status == "completed")
    ):
        return existing, False
    db.execute(  # serialise the count-then-insert of this workspace (no two requests both see room)
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"ai-active:{who.tenant_id}"}
    )
    active = db.scalar(
        select(func.count())
        .select_from(DocumentExtraction)
        .where(DocumentExtraction.tenant_id == who.tenant_id, DocumentExtraction.status.in_(ACTIVE))
    )
    if (active or 0) >= settings.ai_max_active_extractions:
        raise TooManyExtractions(headers={"Retry-After": "30"})
    new_row = new_id()
    # column list = the INSERT grant of rebate_app: it can queue a request, never write a result
    db.execute(
        insert(DocumentExtraction).values(
            tenant_id=who.tenant_id,
            id=new_row,
            file_id=file_id,
            kind="agreement",
            status="queued",
            created_by=who.user_id,
            created_at=clock.now(),
        )
    )
    row = get_extraction(db, who.tenant_id, new_row)
    enqueue(
        db,
        JOB,
        tenant_id=who.tenant_id,
        payload={"extraction_id": str(row.id)},
        created_by=who.user_id,
        dedupe_key=f"{JOB}:{row.id}",
    )
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=Actor("user", who.user_id),
        action=Action.EXTRACTION_REQUESTED,
        entity_type="document_extraction",
        entity_id=row.id,
        after={"file_id": file_id, "kind": "agreement", "status": "queued"},
        meta=who.meta,
    )
    return row, True


def get_extraction(db: Session, tenant_id: UUID, extraction_id: UUID) -> DocumentExtraction:
    row = db.scalar(
        select(DocumentExtraction).where(
            DocumentExtraction.tenant_id == tenant_id, DocumentExtraction.id == extraction_id
        )
    )
    if row is None:
        raise NotFound()
    return row


def list_extractions(
    db: Session, tenant_id: UUID, file_id: UUID | None, limit: int
) -> list[DocumentExtraction]:
    q = select(DocumentExtraction).where(DocumentExtraction.tenant_id == tenant_id)
    if file_id is not None:
        q = q.where(DocumentExtraction.file_id == file_id)
    return list(
        db.scalars(
            q.order_by(DocumentExtraction.created_at.desc(), DocumentExtraction.id.desc()).limit(limit)
        )
    )


def draft_of(db: Session, tenant_id: UUID, extraction_id: UUID) -> tuple[UUID, str] | None:
    row = db.execute(
        text("SELECT id, status FROM agreement_drafts WHERE tenant_id = :t AND extraction_id = :e"),
        {"t": tenant_id, "e": extraction_id},
    ).first()
    return None if row is None else (row[0], row[1])


def draft_id_of(db: Session, tenant_id: UUID, extraction_id: UUID) -> UUID | None:
    found = draft_of(db, tenant_id, extraction_id)
    return None if found is None else found[0]


def usage(db: Session, tenant_id: UUID, settings: Settings, days: int = 30) -> dict[str, Any]:
    """Per-tenant AI usage: today against the caps, and per-day totals (``ai_invocations`` is the ledger)."""
    now = clock.now().astimezone(UTC)
    today = datetime.combine(now.date(), time.min, tzinfo=UTC)
    since = today - timedelta(days=days - 1)
    day = func.date_trunc("day", func.timezone("UTC", AiInvocation.created_at))
    rows = db.execute(
        select(
            day.label("day"),
            func.count(),
            func.coalesce(func.sum(AiInvocation.input_tokens), 0),
            func.coalesce(func.sum(AiInvocation.output_tokens), 0),
            func.coalesce(func.sum(AiInvocation.cost_usd), 0),
        )
        .where(AiInvocation.tenant_id == tenant_id, AiInvocation.created_at >= since)
        .group_by(day)
        .order_by(day)
    ).all()
    daily = [
        {
            "date": d.date().isoformat(),
            "calls": int(n),
            "input_tokens": int(i),
            "output_tokens": int(o),
            "cost_usd": format(Decimal(c), "f"),
        }
        for d, n, i, o, c in rows
    ]
    spent = next((Decimal(r["cost_usd"]) for r in daily if r["date"] == today.date().isoformat()), Decimal(0))
    tokens = next(
        (r["input_tokens"] + r["output_tokens"] for r in daily if r["date"] == today.date().isoformat()), 0
    )
    return {
        "enabled": tenancy.get_settings(db, tenant_id).ai_processing_enabled,
        "model": settings.ai_model,
        "today": {
            "cost_usd": format(spent, "f"),
            "tokens": tokens,
            "cost_cap_usd": format(settings.ai_daily_cost_cap_usd, "f"),
            "token_cap": settings.ai_daily_token_cap,
        },
        "daily": daily,
    }


# ---- result view ----------------------------------------------------------------------------------------


def _evidence(r: Any) -> dict[str, Any] | None:
    if r is None or r.span is None:
        return None
    s = r.span
    return {
        "page": s.page,
        "sheet": s.sheet,
        "cell": s.cell,
        "quote": s.text,
        "start": s.start,
        "end": s.end,
        "boxes": [list(b) for b in s.boxes],
    }


def _pass_view(r: Any) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "status": r.status.value,
        "value": payload.jsonable(r.claimed),
        "reasons": list(r.reasons),
        "evidence": _evidence(r),
    }


def field_view(f: MergedField, paths: dict[str, str] | None = None) -> dict[str, Any]:
    verified = next((r for r in (f.a, f.b) if r is not None and r.status is Status.verified), None)
    return {
        "key": f.key,
        "payload_path": (paths or {}).get(f.key),
        "critical": f.critical,
        "value": payload._val(f),
        "confidence": f.confidence.label,
        "reasons": list(f.reasons),
        "evidence": _evidence(verified),
        "pass_a": _pass_view(f.a),
        "pass_b": _pass_view(f.b),
    }


def indicator_view(report: VerificationReport) -> list[dict[str, Any]]:
    return [
        {
            "kind": i.kind.value,
            "excerpt": i.excerpt[:200],
            "page": i.page,
            "sheet": i.sheet,
            "cell": i.cell,
            "start": i.start,
            "end": i.end,
        }
        for i in report.indicators
    ]


def _warned_keys(warnings: list[dict[str, str]], fields: dict[str, MergedField]) -> frozenset[str]:
    """Validator warnings -> merged field keys (they lower those fields' confidence one step)."""
    items = [k for k, _ in payload.rows_of(fields, "items")]
    customers = [k for k, _ in payload.rows_of(fields, "customers")]
    alias = {"sku": "part_number", "claim_uom": "uom"}
    header_alias = {"manufacturer_code": "manufacturer_name"}
    out: set[str] = set()
    for w in warnings:
        path = w.get("field", "")
        parts = path.removeprefix("payload.").split(".")
        if len(parts) >= 2 and parts[0] in ("header", "rules"):
            name = header_alias.get(parts[1], parts[1]) if parts[0] == "header" else parts[1]
            for cand in (f"{parts[0]}.{name}", f"header.{name}", f"rules.{name}", f"rules.{name}_policy"):
                if cand in fields:
                    out.add(cand)
            if parts[1] == "calculation" and len(parts) > 2:
                out.add("rules.calculation_basis" if parts[2] == "basis" else "rules.cost_source")
        elif parts and parts[0].startswith(("items[", "customers[")):
            name, _, idx = parts[0].partition("[")
            n = int(idx.rstrip("]")) if idx.rstrip("]").isdigit() else -1
            keys = items if name == "items" else customers
            if 0 <= n < len(keys):
                field = alias.get(parts[1], parts[1]) if len(parts) > 1 and name == "items" else "name"
                out.add(f"{name}[{keys[n]}].{field}")
    return frozenset(k for k in out if k in fields)


# ---- the job -----------------------------------------------------------------------------------------------


def _mask_cards(value: str) -> str:
    while (hit := ingestion.find_card_number(value)) is not None:
        value = value.replace(hit, ingestion.CARD_REMOVED)
    return value


def _format_errors(raw: Any) -> list[str]:
    try:
        flatten(payload.normalise_rates(raw))
    except CandidateFormatError as exc:
        return [str(exc)]
    return []


def _check_format(raw: dict[str, Any]) -> None:
    if errors := _format_errors(raw):
        raise ValueError("; ".join(errors))


def _master_names(db: Session, tenant_id: UUID) -> tuple[dict[str, str], dict[str, str]]:
    manufacturers = {
        payload.name_key(n): c
        for n, c in db.execute(
            text("SELECT name, code FROM manufacturers WHERE tenant_id = :t"), {"t": tenant_id}
        )
    }
    customers: dict[str, str] = {}
    for alias, code in db.execute(
        text(
            "SELECT a.alias, c.code FROM customer_aliases a JOIN customers c "
            "ON c.tenant_id = a.tenant_id AND c.id = a.customer_id WHERE a.tenant_id = :t AND c.code IS NOT NULL"
        ),
        {"t": tenant_id},
    ):
        customers[payload.name_key(alias)] = code
    for name, code in db.execute(
        text("SELECT name, code FROM customers WHERE tenant_id = :t AND code IS NOT NULL"), {"t": tenant_id}
    ):
        customers[payload.name_key(name)] = code
    return manufacturers, customers


class _Superseded(Exception):
    pass


class _Stop(Exception):
    def __init__(self, status: str, code: str, detail: str | None = None, *, draft: bool = False) -> None:
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail
        self.draft = draft  # also create a needs_manual_entry draft so the reviewer has somewhere to start


def _finish_audit(
    db: Session, ex: DocumentExtraction, status: str, code: str | None, draft_id: UUID | None, **extra: Any
) -> None:
    audit.record(
        db,
        tenant_id=ex.tenant_id,
        actor=Actor("system"),
        action=Action.EXTRACTION_FINISHED,
        entity_type="document_extraction",
        entity_id=ex.id,
        after={
            "file_id": ex.file_id,
            "kind": ex.kind,
            "status": status,
            "failure": code,
            "draft_id": draft_id,
            **extra,
        },
    )


def _audit_once(
    db: Session, ex: DocumentExtraction, status: str, code: str | None, draft_id: UUID | None, **extra: Any
) -> None:
    seen = db.scalar(
        text("SELECT 1 FROM audit_log WHERE tenant_id = :t AND entity_id = :e AND action = :a LIMIT 1"),
        {"t": ex.tenant_id, "e": ex.id, "a": Action.EXTRACTION_FINISHED.value},
    )
    if not seen:
        _finish_audit(db, ex, status, code, draft_id, **extra)


def audit_invocations(engine: Engine, tenant_id: UUID, items: list[dict[str, Any]]) -> None:
    """One ``ai.invocation`` audit event per model call (cost, outcome, purpose; never content), in a
    transaction of its own so failed calls are recorded too."""
    if not items:
        return
    with transaction(engine, tenant_id) as db:
        for i in items:
            audit.record(
                db,
                tenant_id=tenant_id,
                actor=Actor("system"),
                action=Action.AI_INVOCATION,
                entity_type="ai_invocation",
                entity_id=i["id"],
                after={k: v for k, v in i.items() if k != "id"},
            )


def _settle(
    engine: Engine,
    ex: DocumentExtraction,
    status: str,
    code: str | None,
    *,
    body: dict[str, Any] | None = None,
    manual: bool = False,
    **extra: Any,
) -> tuple[UUID | None, dict[str, Any]]:
    """The app-side effects of finishing an extraction (the draft and the ``extraction.finished`` audit entry),
    committed in a transaction of their own **before** the AI role writes the final status. A crash anywhere
    leaves the extraction unfinished (the job retries and gets here again) and never a final status without
    its draft and audit entry. Idempotent: an existing draft/audit entry is reused."""
    with transaction(engine, ex.tenant_id) as d:
        # lock the row: a concurrent reaper/dead-letter that failed it must not be followed by a draft
        current = d.scalar(
            text("SELECT status FROM document_extractions WHERE tenant_id = :t AND id = :i FOR UPDATE"),
            {"t": ex.tenant_id, "i": ex.id},
        )
        if current in FINAL and current != status:
            raise _Superseded
        stored = d.scalar(
            text(
                "SELECT after ->> 'status' FROM audit_log WHERE tenant_id = :t AND entity_id = :e "
                "AND action = :a ORDER BY occurred_at LIMIT 1"
            ),
            {"t": ex.tenant_id, "e": ex.id, "a": Action.EXTRACTION_FINISHED.value},
        )
        if stored is not None and stored != status:  # a retry must not contradict what was audited
            raise PermanentError(f"extraction already settled as {stored}, refusing to settle as {status}")
        found = draft_id_of(d, ex.tenant_id, ex.id)
        if found is not None:
            validation = d.scalar(
                text("SELECT validation FROM agreement_drafts WHERE tenant_id = :t AND id = :i"),
                {"t": ex.tenant_id, "i": found},
            )
            _audit_once(d, ex, status, code, found, **extra)
            return found, dict(validation or {})
        if body is None and not manual:
            _audit_once(d, ex, status, code, None, **extra)
            return None, {}
        draft = agreements.create_ai_draft(
            d,
            Who(ex.tenant_id, ex.created_by, {}),
            payload=body if body is not None else {"payload_schema_version": 1},
            source_file_id=ex.file_id,
            extraction_id=ex.id,
            needs_manual_entry=manual,
        )
        _finish_audit(d, ex, status, code, draft.id, **extra)
        return draft.id, dict(draft.validation or {})


def _load_document(data: bytes, mime: str) -> Any:
    return extract_pdf(data) if mime == PDF_MIME else extract_xlsx(data)


def _stop_row(ex: DocumentExtraction, stop: _Stop) -> dict[str, Any]:
    return {
        "status": stop.status,
        "error_code": stop.code,
        "error_detail": (stop.detail or "")[:500] or None,
        "completed_at": clock.now(),
    }


# ---- unfinished rows must never block a file ----------------------------------------------------------


def _fail_unfinished(db: Session, tenant_id: UUID, code: str, detail: str, **where: Any) -> int:
    """Mark queued/processing rows failed with the app role's narrow UPDATE grant (trigger-enforced)."""
    sql = (
        "UPDATE document_extractions SET status = 'failed', error_code = :c, error_detail = :d, completed_at = :n "
        "WHERE tenant_id = :t AND status IN ('queued', 'processing')"
    )
    if "extraction_id" in where:
        sql += " AND id = :i"
    if "older_than" in where:  # DB time, like updated_at (the progress marker every AI-role write refreshes)
        sql += " AND updated_at < now() - make_interval(secs => :b)"
    result = db.execute(
        text(sql),
        {
            "c": code,
            "d": detail,
            "n": clock.now(),
            "t": tenant_id,
            "i": where.get("extraction_id"),
            "b": where.get("older_than"),
        },
    )
    return int(getattr(result, "rowcount", 0) or 0)


def reap_stale(db: Session, tenant_id: UUID) -> int:
    return _fail_unfinished(
        db,
        tenant_id,
        "stale",
        "The extraction did not finish in time; enter manually or start it again.",
        older_than=STALE_AFTER.total_seconds(),
    )


def on_dead(db: Session, ctx: JobContext) -> None:
    """Dead-letter hook: the job will not run again, so its extraction must not stay queued/processing."""
    if ctx.tenant_id is None:
        return
    try:
        extraction_id = UUID(ctx.payload["extraction_id"])
    except KeyError, ValueError:
        return
    _fail_unfinished(
        db,
        ctx.tenant_id,
        "job_failed",
        "The extraction job did not finish; enter manually or start it again.",
        extraction_id=extraction_id,
    )


# ---- the job ---------------------------------------------------------------------------------------------


def process(db: Session, ctx: JobContext) -> None:
    """``extract_agreement`` job body (tenant-scoped, ``rebate_app`` session ``db`` for reads)."""
    tenant = ctx.tenant_id
    if tenant is None:
        raise PermanentError("tenant-scoped job without tenant context")
    if ctx.engine is None:
        raise PermanentError("the extraction job needs the worker engine")
    try:
        ex = get_extraction(db, tenant, UUID(ctx.payload["extraction_id"]))
    except (KeyError, ValueError, NotFound) as exc:
        raise PermanentError("extraction not found") from exc
    try:
        ai = ai_engine(ctx.settings)
    except gateway.NotConfigured as exc:  # operator error: dead-letter (on_dead fails the row, visibly)
        raise PermanentError("the AI gateway is not configured (DATABASE_URL_AI)") from exc
    if ex.status in FINAL:
        if ex.status == "completed" and ex.result:
            # legacy/crash recovery: a completed row always has its draft and audit entry (idempotent)
            _settle(ctx.engine, ex, "completed", None, body=ex.result["payload"])
        return
    try:
        _run(db, ctx, ex, ai)
    except _Superseded:
        log.info("extraction.superseded", extraction_id=str(ex.id))
    except _Stop as stop:
        try:
            _settle(ctx.engine, ex, stop.status, stop.code, manual=stop.draft)
        except _Superseded:
            return
        _write(ai, tenant, ex.id, **_stop_row(ex, stop))  # last: the draft and audit entry are durable
        log.info("extraction.stopped", extraction_id=str(ex.id), status=stop.status, code=stop.code)


def _run(db: Session, ctx: JobContext, ex: DocumentExtraction, ai: Engine) -> None:
    settings, tenant = ctx.settings, ex.tenant_id
    if ctx.engine is None:  # checked in process; narrows the type
        raise PermanentError("the extraction job needs the worker engine")
    enabled = tenancy.get_settings(db, tenant).ai_processing_enabled
    if not enabled:
        raise _Stop("needs_manual_entry", "ai_disabled", "AI processing is not enabled for this workspace.")
    file = files.get(db, tenant, ex.file_id)
    data = files.read_clean(settings, file)
    doc = _load_document(data, file.detected_mime)
    if isinstance(doc, ExtractionFailure):
        raise _Stop("needs_manual_entry", doc.reason.value, doc.detail, draft=True)
    text_method = "native"
    if isinstance(doc, PdfText) and doc.needs_ocr_pages:
        doc, failure = ocr.apply_ocr(
            data,
            doc,
            DEFAULT_LIMITS,
            timeout_seconds=settings.ocr_timeout_seconds,
            memory_mb=settings.ocr_memory_mb,
        )
        text_method = "ocr" if any(p.text_method == "ocr" for p in doc.pages) else "native"
        if failure is not None:
            log.warning("extraction.ocr_failed", extraction_id=str(ex.id), reason=failure)
    prompt_text = _mask_cards(prompts.serialise(doc))
    if not prompt_text.strip() or (isinstance(doc, PdfText) and not any(p.text.strip() for p in doc.pages)):
        raise _Stop("needs_manual_entry", "no_text", "No readable text in the document.", draft=True)
    if len(prompt_text) > MAX_PROMPT_CHARS:
        raise _Stop(
            "needs_manual_entry",
            "too_long_for_model",
            "Document too long to send in one request.",
            draft=True,
        )
    _write(
        ai, tenant, ex.id, status="processing", text_method=text_method, prompt_version=prompts.PROMPT_VERSION
    )

    passes: dict[str, Any] = dict(ex.pass_results or {})
    gw: gateway.Gateway | None = None
    try:
        gw = gateway.Gateway(settings, ai, gateway.make_provider(settings))
        for variant, model in (
            ("a", settings.ai_model),
            ("b", settings.ai_model_pass_b or settings.ai_model),
        ):
            if variant in passes:
                continue  # a job retry never pays for a pass twice
            system, user = prompts.build_agreement_prompt(variant, prompt_text)
            result = gw.call(
                ai_enabled=enabled,
                tenant_id=tenant,
                extraction_id=ex.id,
                purpose=f"agreement_pass_{variant}",
                model=model,
                prompt_version=prompts.PROMPT_VERSION,
                system=system,
                user=user,
                tool=prompts.TOOL_NAME,
                schema=prompts.extraction_schema(),
                validate=_check_format,
            )
            passes[variant] = {"model": model, "repaired": result.repaired, "output": result.data}
            _write(ai, tenant, ex.id, pass_results=passes, model=settings.ai_model)
    except gateway.AiDisabled as exc:
        raise _Stop("needs_manual_entry", exc.code, str(exc)) from exc
    except gateway.CapReached as exc:
        raise _Stop(
            "failed", exc.code, "The workspace's daily AI limit is reached; enter manually or retry tomorrow."
        ) from exc
    except gateway.ProviderRefused as exc:
        raise _Stop("needs_manual_entry", exc.code, str(exc), draft=True) from exc
    except gateway.ProviderUnavailable as exc:
        if ctx.attempt < JOB_MAX_ATTEMPTS:
            raise  # job-level retry with backoff (bounded); the passes saved so far are reused
        raise _Stop("failed", exc.code, "The AI provider could not be reached; enter manually.") from exc
    except (
        gateway.InvalidOutput,
        gateway.NotConfigured,
        gateway.ProviderFailure,
        gateway.ProviderRejected,  # permanent 4xx: no job retry, the user sees the real reason
    ) as exc:
        raise _Stop("failed", exc.code, str(exc)) from exc
    finally:
        if gw is not None:
            audit_invocations(ctx.engine, tenant, gw.take_invocations())

    raw_a, raw_b = (payload.normalise_rates(passes[v]["output"]) for v in ("a", "b"))
    try:
        report = run_verification(doc, raw_a, raw_b, schema_validator=_format_errors)
    except CandidateFormatError as exc:
        raise _Stop("failed", "invalid_output", str(exc)) from exc
    manufacturers, customers = _master_names(db, tenant)

    if report.needs_manual_entry:
        raise _Stop(
            "needs_manual_entry",
            "nothing_verified",
            "No critical field could be verified against the document.",
            draft=True,
        )
    body = payload.build_payload(report.fields, manufacturer_codes=manufacturers, customer_codes=customers)
    draft_id, validation = _settle(
        ctx.engine,
        ex,
        "completed",
        None,
        body=body,
        model=settings.ai_model,
        prompt_version=prompts.PROMPT_VERSION,
    )
    warned = _warned_keys(list(validation.get("warnings", [])), report.fields)
    if warned:  # validator warnings lower the affected fields one confidence step (§6)
        report = run_verification(doc, raw_a, raw_b, schema_validator=_format_errors, warned_keys=warned)
    paths = payload.payload_paths(report.fields)
    fields = [field_view(f, paths) for f in report.fields.values()]
    outcome = {
        "schema_version": 1,
        "agreement_type": report.agreement_type,
        "page_count": len(doc.pages) if isinstance(doc, PdfText) else None,
        "payload": body,
        "fields": fields,
        "blocking": list(report.blocking),
        "needs_acknowledgement": list(report.needs_acknowledgement),
        "indicators": indicator_view(report),
        "draft_id": str(draft_id),
        "notes": {v: _notes(passes[v]["output"]) for v in ("a", "b")},
        "unresolved": validation.get("references", {}),
    }
    evidence_check = {
        f["key"]: {
            "a": f["pass_a"] and f["pass_a"]["status"],
            "b": f["pass_b"] and f["pass_b"]["status"],
            "reasons": f["reasons"],
        }
        for f in fields
    }
    _write(  # last: the draft and the audit entry are already durable
        ai,
        tenant,
        ex.id,
        status="completed",
        result=outcome,
        evidence_check=evidence_check,
        validation=validation,
        confidence=NUMERIC[report.draft_confidence],
        error_code=None,
        completed_at=clock.now(),
    )
    log.info(
        "extraction.completed", extraction_id=str(ex.id), fields=len(fields), blocking=len(report.blocking)
    )


MAX_NOTES = 20
MAX_NOTE_CHARS = 500


def _notes(output: dict[str, Any]) -> list[str]:
    """Free text the model added for the reviewer: bounded in count and length, never executed."""
    raw = output.get("extractor_notes") or []
    return [str(n)[:MAX_NOTE_CHARS] for n in raw[:MAX_NOTES]] if isinstance(raw, list) else []
