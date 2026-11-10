"""Import pipeline services (FR-IM-01..08, ARCHITECTURE.md W3/W4, §6 jobs).

Job chain (``core/jobs.py``): ``POST /imports`` -> ``import_parse`` (waits for the malware scan, stages
raw rows, validates once a mapping exists) -> user confirms -> ``import_load`` (one chunk per job).

* ``import_parse`` runs in one transaction and renews its lease while it streams rows, so a parse that
  outlives the 10-minute lease is not reclaimed; a crash rolls everything back and the retry starts
  over (nothing partial is ever visible).
* ``import_load`` loads ``LOAD_CHUNK`` staged rows per job: rows, counts and the checkpoint commit
  together with the job's success, and the job for the next chunk is enqueued in the same
  transaction. A crash or a lost lease rolls the chunk back; the reclaimed job resumes from the last
  committed checkpoint, and the natural-key upsert makes a repeated chunk a no-op (no duplicates).
"""

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebate.core import clock, jobs
from rebate.core.errors import Conflict, InvalidState, NotFound, PreconditionFailed, ValidationFailed
from rebate.core.ids import new_id
from rebate.core.jobs import JobContext
from rebate.modules import audit, files, masterdata, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.ingestion import loader, readers
from rebate.modules.ingestion import normalizers as n
from rebate.modules.ingestion.models import ImportBatch, ImportRowError, ImportStagedRow, MappingTemplate
from rebate.modules.ingestion.validators import (
    FIELDS,
    PAN_MARK,
    Issue,
    Lookups,
    MappingInvalid,
    RowValidator,
    check_mapping,
    suggest_mapping,
)

STAGE_BATCH = 5000
LOAD_CHUNK = 5000
MAX_ROWS = 2_000_000
SCAN_WAIT = timedelta(hours=1)
SCAN_POLL = timedelta(seconds=5)
TERMINAL = frozenset({"completed", "completed_with_errors", "failed", "rejected"})


class ImportHasErrors(Conflict):
    code = "import_has_errors"
    message = "Some rows have errors. Fix the file and import it again, or confirm with accept_partial to load only the valid rows."


def _tenant(ctx: JobContext) -> UUID:
    if ctx.tenant_id is None:  # the runner refuses tenant-scoped jobs without one
        raise jobs.PermanentError("tenant-scoped job without tenant context")
    return ctx.tenant_id


def _queued(job_id: UUID | None) -> UUID:
    if job_id is None:  # dedupe keys embed fresh ids, so this cannot happen
        raise RuntimeError("job was not enqueued")
    return job_id


def _record(
    db: Session, batch: ImportBatch, action: Action, actor: Actor, meta: dict[str, Any], **kw: Any
) -> None:
    audit.record(
        db,
        tenant_id=batch.tenant_id,
        actor=actor,
        action=action,
        entity_type="import_batch",
        entity_id=batch.id,
        meta=meta,
        **kw,
    )


def get_batch(db: Session, tenant_id: UUID, batch_id: UUID, *, lock: bool = False) -> ImportBatch:
    stmt = select(ImportBatch).where(ImportBatch.tenant_id == tenant_id, ImportBatch.id == batch_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def list_batches(
    db: Session,
    tenant_id: UUID,
    *,
    status: str | None,
    import_type: str | None,
    limit: int,
    cursor: str | None,
) -> list[ImportBatch]:
    stmt = select(ImportBatch).where(ImportBatch.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(ImportBatch.status == status)
    if import_type is not None:
        stmt = stmt.where(ImportBatch.import_type == import_type)
    return list(db.scalars(masterdata.keyset(stmt, ImportBatch.id, cursor, limit)))


def _file_type(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower()


def create_import(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    file_id: UUID,
    import_type: str,
    mapping_template_id: UUID | None,
    source_system: str,
    meta: dict[str, Any],
) -> tuple[ImportBatch, UUID]:
    try:
        file = files.get(db, tenant_id, file_id)
    except NotFound:
        raise ValidationFailed(
            details=[{"field": "file_id", "code": "not_found", "message": "Unknown file."}]
        ) from None
    if file.kind != "import" or _file_type(file.original_name) not in ("csv", "xlsx"):
        raise ValidationFailed(
            details=[
                {"field": "file_id", "code": "wrong_kind", "message": "Upload the file with kind 'import'."}
            ]
        )
    if file.scan_status in ("infected", "error"):
        raise InvalidState("This file did not pass the malware scan and cannot be imported.")
    active = db.scalar(
        select(ImportBatch.id).where(
            ImportBatch.tenant_id == tenant_id,
            ImportBatch.file_id == file_id,
            ImportBatch.status.not_in(TERMINAL),
        )
    )
    if active is not None:
        raise Conflict("An import of this file is already in progress.")
    template = None
    if mapping_template_id is not None:
        template = _template(db, tenant_id, mapping_template_id)
        if template.import_type != import_type:
            raise ValidationFailed(
                details=[
                    {
                        "field": "mapping_template_id",
                        "code": "wrong_type",
                        "message": "The template is for another import type.",
                    }
                ]
            )
    now = clock.now()
    batch = ImportBatch(
        tenant_id=tenant_id,
        id=new_id(),
        import_type=import_type,
        file_id=file_id,
        mapping_template_id=template.id if template else None,
        source_system=source_system,
        status="parsing" if file.scan_status == "clean" else "scanning",
        created_by=user_id,
        created_at=now,
        updated_at=now,
        counts={},
        checkpoint={},
        options={},
    )
    db.add(batch)
    db.flush()
    job_id = _queued(jobs.enqueue(
        db, "import_parse", tenant_id=tenant_id, payload={"batch_id": str(batch.id)},
        created_by=user_id, dedupe_key=f"import_parse:{batch.id}:0",
    ))  # fmt: skip
    _record(
        db, batch, Action.IMPORT_CREATED, Actor("user", user_id), meta,
        after={"import_type": import_type, "file_id": file_id, "source_system": source_system,
               "mapping_template_id": batch.mapping_template_id, "status": batch.status},
    )  # fmt: skip
    return batch, job_id


# ---- parse (job) ----------------------------------------------------------------------------------


def _fail(db: Session, batch: ImportBatch, status: str, summary: str, code: str | None = None) -> None:
    batch.status, batch.error_summary, batch.updated_at = status, summary[:500], clock.now()
    if code is not None:
        db.add(
            ImportRowError(
                tenant_id=batch.tenant_id, id=new_id(), import_batch_id=batch.id, row_number=None,
                code=code, message=summary[:500], severity="error",
            )
        )  # fmt: skip
    db.flush()
    _record(db, batch, Action.IMPORT_FAILED, Actor("system"), {"request_id": "job"}, after={"status": status})


def parse(db: Session, ctx: JobContext) -> None:
    batch = get_batch(db, _tenant(ctx), UUID(ctx.payload["batch_id"]), lock=True)
    if batch.status not in ("scanning", "parsing"):
        return  # already parsed, confirmed or finished: a repeated job is a no-op
    file = files.get(db, batch.tenant_id, batch.file_id)
    now = clock.now()
    if file.scan_status == "pending":
        if now - batch.created_at > SCAN_WAIT:
            _fail(db, batch, "failed", "The malware scan did not finish in time; start the import again.")
            return
        batch.status = "scanning"
        jobs.enqueue(
            db,
            "import_parse",
            tenant_id=batch.tenant_id,
            payload={"batch_id": str(batch.id)},
            run_at=now + SCAN_POLL,
        )
        return
    if file.scan_status != "clean":
        _fail(db, batch, "rejected", "The file did not pass the malware scan.")
        return
    batch.status, batch.started_at, batch.updated_at = "parsing", batch.started_at or now, now
    if batch.headers is None:
        try:
            with db.begin_nested():  # an unreadable file leaves no staged rows behind
                _stage(db, ctx, batch, file)
        except (readers.ReadError, files.UndecodableText) as exc:
            _fail(db, batch, "failed", exc.message, exc.code)
            return
    if batch.mapping is None:
        _apply_saved_template(db, batch)
    if batch.mapping is None:
        batch.status = "awaiting_confirmation"  # the user maps the columns (preview shows a suggestion)
        db.flush()
        return
    validate(db, ctx, batch)
    batch.status, batch.updated_at = "awaiting_confirmation", clock.now()
    db.flush()


def _stage(db: Session, ctx: JobContext, batch: ImportBatch, file: files.FileObject) -> None:
    data = files.read_clean(ctx.settings, file)
    file_type = _file_type(file.original_name)
    opts = batch.options or {}
    source = (
        readers.xlsx_source(data, opts.get("sheet"))
        if file_type == "xlsx"
        else nullcontext(readers.csv_source(data, opts.get("delimiter")))
    )
    with source as src:
        recs = readers.records(src, opts.get("header_row"))
        batch.detected_encoding, batch.delimiter, batch.sheet_name = (
            src.encoding,
            src.delimiter,
            src.sheet_name,
        )
        batch.header_row, batch.headers = recs.header_row, recs.headers
        batch.header_signature = n.header_signature(recs.headers)
        total = 0
        for chunk in _chunks(recs.rows, STAGE_BATCH):
            total += len(chunk)
            if total > MAX_ROWS:
                raise readers.ReadError("too_many_rows", f"At most {MAX_ROWS:,} rows per file.")
            db.execute(
                insert(ImportStagedRow),
                [
                    {
                        "tenant_id": batch.tenant_id,
                        "import_batch_id": batch.id,
                        "row_number": number,
                        "raw": _pan_safe(raw),
                    }
                    for number, raw in chunk
                ],
            )
            ctx.renew_lease()
    if total == 0:
        raise readers.ReadError("empty_file", "The file has a header but no data rows.")
    batch.counts = {"rows_total": total}
    db.flush()


def _chunks(
    rows: Iterator[tuple[int, dict[str, str]]], size: int
) -> Iterator[list[tuple[int, dict[str, str]]]]:
    chunk: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _pan_safe(raw: dict[str, str]) -> dict[str, str]:
    """PAN guard: card-like numbers never reach the database (SECURITY.md §5)."""
    hits = [k for k, v in raw.items() if n.find_card_number(v) is not None]
    if not hits:
        return raw
    clean = {k: (n.mask_card_number(v) if k in hits else v) for k, v in raw.items()}
    clean[PAN_MARK] = "\x1f".join(hits)
    return clean


def _apply_saved_template(db: Session, batch: ImportBatch) -> None:
    """An explicitly chosen template, else the newest template with this header signature."""
    if batch.mapping_template_id is not None:
        template: MappingTemplate | None = _template(db, batch.tenant_id, batch.mapping_template_id)
    else:
        template = db.scalar(
            select(MappingTemplate)
            .where(
                MappingTemplate.tenant_id == batch.tenant_id,
                MappingTemplate.import_type == batch.import_type,
                MappingTemplate.header_signature == batch.header_signature,
            )
            .order_by(MappingTemplate.updated_at.desc(), MappingTemplate.id.desc())
            .limit(1)
        )
    if template is None:
        return
    try:
        check_mapping(batch.import_type, template.mapping, template.options, batch.headers or [])
    except MappingInvalid:
        batch.mapping_template_id = None  # the template does not fit this file: the user maps it
        return
    batch.mapping_template_id, batch.mapping_template_version = template.id, template.version
    batch.mapping, batch.options = template.mapping, {**template.options, **_reader_options(batch.options)}


def _reader_options(options: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (options or {}).items() if k in ("delimiter", "sheet", "header_row")}


def _lookups(db: Session, batch: ImportBatch) -> Lookups:
    index = masterdata.master_index(db, batch.tenant_id)
    currency = tenancy.get_settings(db, batch.tenant_id).currency_code
    return Lookups(
        customers=index.customers,
        products=index.products,
        branches=index.branches,
        manufacturers=index.manufacturers,
        currency=currency,
        sales_line_exists=lambda keys: loader.sales_lines_exist(
            db, batch.tenant_id, batch.source_system, keys
        ),
    )


def validate(db: Session, ctx: JobContext, batch: ImportBatch) -> None:
    """(Re)normalise and validate every staged row with the batch mapping; rewrite the row errors."""
    if batch.mapping is None:
        raise InvalidState("validation needs a mapping")
    db.execute(
        delete(ImportRowError).where(
            ImportRowError.tenant_id == batch.tenant_id, ImportRowError.import_batch_id == batch.id
        )
    )
    validator = RowValidator(batch.import_type, batch.mapping, batch.options or {}, _lookups(db, batch))
    after = 0
    counts = {"rows_total": 0, "valid": 0, "rejected": 0, "duplicates": 0, "warnings": 0}
    while True:
        staged = list(
            db.execute(
                select(ImportStagedRow.row_number, ImportStagedRow.raw)
                .where(ImportStagedRow.import_batch_id == batch.id, ImportStagedRow.row_number > after)
                .order_by(ImportStagedRow.row_number)
                .limit(STAGE_BATCH)
            )
        )
        if not staged:
            break
        updates, errors = [], []
        for number, raw in staged:
            result = validator.validate(number, raw)
            updates.append(
                {
                    "import_batch_id": batch.id, "row_number": number, "normalized": result.normalized,
                    "natural_key": result.natural_key, "row_hash": result.row_hash, "phase": result.phase,
                    "status": result.status,
                }
            )  # fmt: skip
            errors.extend(_error_rows(batch, number, raw, result.issues))
        db.execute(update(ImportStagedRow), updates)
        if errors:
            db.execute(insert(ImportRowError), errors)
        after = staged[-1][0]
        ctx.renew_lease()
    for number, issues in validator.finish().items():
        raw = db.scalar(
            select(ImportStagedRow.raw).where(
                ImportStagedRow.import_batch_id == batch.id, ImportStagedRow.row_number == number
            )
        )
        db.execute(insert(ImportRowError), _error_rows(batch, number, raw or {}, issues))
        if any(i.severity == "error" for i in issues):
            db.execute(
                update(ImportStagedRow)
                .where(ImportStagedRow.import_batch_id == batch.id, ImportStagedRow.row_number == number)
                .values(status="error", normalized=None, natural_key=None, row_hash=None)
            )
    by_status = dict(
        db.execute(
            select(ImportStagedRow.status, func.count())
            .where(ImportStagedRow.import_batch_id == batch.id)
            .group_by(ImportStagedRow.status)
        ).tuples()
    )
    counts["valid"], counts["rejected"], counts["duplicates"] = (
        by_status.get("valid", 0), by_status.get("error", 0), by_status.get("duplicate", 0),
    )  # fmt: skip
    counts["rows_total"] = sum(by_status.values())
    counts["warnings"] = (
        db.scalar(
            select(func.count()).where(
                ImportRowError.tenant_id == batch.tenant_id,
                ImportRowError.import_batch_id == batch.id,
                ImportRowError.severity == "warning",
            )
        )
        or 0
    )
    batch.counts = counts
    if validator.unmapped:
        masterdata.record_unmapped(
            db,
            batch.tenant_id,
            batch.id,
            {k: (u.count, u.example_name) for k, u in validator.unmapped.items()},
        )
    db.flush()


def _error_rows(
    batch: ImportBatch, number: int, raw: dict[str, str], issues: list[Issue]
) -> list[dict[str, Any]]:
    shown = {k: v for k, v in raw.items() if not k.startswith("\x1f")}
    return [
        {
            "tenant_id": batch.tenant_id, "id": new_id(), "import_batch_id": batch.id, "row_number": number,
            "column_name": (i.column or None) and i.column[:200], "code": i.code, "message": i.message[:500],
            "severity": i.severity, "raw": shown,
        }
        for i in issues
    ]  # fmt: skip


# ---- mapping, confirm ---------------------------------------------------------------------------


def set_mapping(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    batch_id: UUID,
    mapping: dict[str, str | None],
    options: dict[str, Any],
    template_name: str | None,
    meta: dict[str, Any],
) -> tuple[ImportBatch, UUID]:
    batch = get_batch(db, tenant_id, batch_id, lock=True)
    if batch.status != "awaiting_confirmation" or batch.headers is None:
        raise InvalidState(
            "The mapping can be set once the file is parsed and before the import is confirmed."
        )
    merged = {**options, **_reader_options(batch.options)}
    try:
        check_mapping(batch.import_type, mapping, merged, batch.headers)
    except MappingInvalid as exc:
        raise ValidationFailed(details=exc.details) from None
    batch.mapping, batch.options = mapping, merged
    batch.mapping_revision += 1
    batch.mapping_template_id, batch.mapping_template_version = None, None
    if template_name is not None:
        template = save_template(
            db, tenant_id=tenant_id, user_id=user_id, name=template_name, import_type=batch.import_type,
            headers=batch.headers, mapping=mapping, options={k: v for k, v in options.items() if k not in ("header_row",)},
            meta=meta, upsert=True,
        )  # fmt: skip
        batch.mapping_template_id, batch.mapping_template_version = template.id, template.version
    batch.status, batch.updated_at = "parsing", clock.now()
    db.flush()
    job_id = _queued(jobs.enqueue(
        db, "import_parse", tenant_id=tenant_id, payload={"batch_id": str(batch.id)}, created_by=user_id,
        dedupe_key=f"import_parse:{batch.id}:{batch.mapping_revision}",
    ))  # fmt: skip
    _record(
        db, batch, Action.IMPORT_MAPPING_SET, Actor("user", user_id), meta,
        after={"mapping_revision": batch.mapping_revision, "mapping_template_id": batch.mapping_template_id},
    )  # fmt: skip
    return batch, job_id


def confirm(
    db: Session, *, tenant_id: UUID, user_id: UUID, batch_id: UUID, accept_partial: bool, meta: dict[str, Any]
) -> tuple[ImportBatch, UUID]:
    batch = get_batch(db, tenant_id, batch_id, lock=True)
    if batch.status != "awaiting_confirmation" or batch.mapping is None:
        raise InvalidState("Only a parsed and mapped import can be confirmed.")
    if batch.counts.get("rejected", 0) and not accept_partial:
        raise ImportHasErrors()
    now = clock.now()
    batch.status, batch.accept_partial, batch.confirmed_by, batch.confirmed_at, batch.updated_at = (
        "loading", accept_partial, user_id, now, now,
    )  # fmt: skip
    batch.checkpoint = {"chunk": 0, "phase": 1, "after_row": 0}
    db.flush()
    job_id = _queued(jobs.enqueue(
        db, "import_load", tenant_id=tenant_id, payload={"batch_id": str(batch.id), "chunk": "0"},
        created_by=user_id, dedupe_key=f"import_load:{batch.id}:0",
    ))  # fmt: skip
    _record(
        db,
        batch,
        Action.IMPORT_CONFIRMED,
        Actor("user", user_id),
        meta,
        after={"accept_partial": accept_partial, "status": "loading"},
    )
    return batch, job_id


# ---- load (job, one chunk per run) --------------------------------------------------------------

LOADED_COUNTS = ("inserted", "unchanged", "changed", "source_changed")


def load_chunk(db: Session, ctx: JobContext) -> None:
    batch = get_batch(db, _tenant(ctx), UUID(ctx.payload["batch_id"]), lock=True)
    cp = dict(batch.checkpoint)
    if batch.status != "loading" or int(ctx.payload["chunk"]) != cp.get("chunk"):
        return  # finished, or a stale duplicate of an already committed chunk
    phase, after = int(cp["phase"]), int(cp["after_row"])
    rows = _valid_rows(db, batch, phase, after)
    while not rows and phase == 1:
        phase, after = 2, 0
        rows = _valid_rows(db, batch, phase, after)
    if not rows:
        _finish(db, batch)
        return
    counts = loader.Counts()
    staged = [loader.Staged(r.row_number, r.normalized or {}, r.row_hash or "") for r in rows]
    _load_rows(db, batch, staged, counts)
    totals = dict(batch.counts)
    for key in LOADED_COUNTS:
        totals[key] = totals.get(key, 0) + getattr(counts, key)
    batch.counts = totals
    next_chunk = int(cp["chunk"]) + 1
    batch.checkpoint = {"chunk": next_chunk, "phase": phase, "after_row": rows[-1].row_number}
    batch.updated_at = clock.now()
    db.flush()
    jobs.enqueue(
        db, "import_load", tenant_id=batch.tenant_id, payload={"batch_id": str(batch.id), "chunk": str(next_chunk)},
        created_by=batch.confirmed_by, dedupe_key=f"import_load:{batch.id}:{next_chunk}",
    )  # fmt: skip


def _valid_rows(db: Session, batch: ImportBatch, phase: int, after: int) -> list[ImportStagedRow]:
    return list(
        db.scalars(
            select(ImportStagedRow)
            .where(
                ImportStagedRow.import_batch_id == batch.id,
                ImportStagedRow.status == "valid",
                ImportStagedRow.phase == phase,
                ImportStagedRow.row_number > after,
            )
            .order_by(ImportStagedRow.row_number)
            .limit(LOAD_CHUNK)
        )
    )


def _load_rows(db: Session, batch: ImportBatch, staged: list[loader.Staged], counts: loader.Counts) -> None:
    if batch.import_type == "sales":
        loader.load_sales(db, batch, staged, counts)
    elif batch.import_type == "purchases":
        loader.load_purchases(db, batch, staged, counts)
    else:
        master = masterdata.LoadCounts()
        rows = [s.normalized for s in staged]
        if batch.import_type == "customers":
            masterdata.load_customers(db, batch.tenant_id, rows, master)
        elif batch.import_type == "products":
            masterdata.load_products(db, batch.tenant_id, rows, master)
        else:
            masterdata.load_uom_conversions(db, batch.tenant_id, rows, master)
        counts.inserted, counts.unchanged, counts.changed = master.inserted, master.unchanged, master.changed
        if batch.import_type in ("customers", "products") and batch.confirmed_by is not None:
            kind, key = (
                ("customer", "customer_code") if batch.import_type == "customers" else ("product", "sku")
            )
            who = masterdata.Who(batch.tenant_id, batch.confirmed_by, {"request_id": f"import:{batch.id}"})
            masterdata.resolve_matching(db, who, kind, [r[key] for r in rows])


def _finish(db: Session, batch: ImportBatch) -> None:
    now = clock.now()
    batch.status = "completed_with_errors" if batch.counts.get("rejected", 0) else "completed"
    batch.completed_at = batch.updated_at = now
    db.flush()
    _record(
        db, batch, Action.IMPORT_COMPLETED, Actor("system"), {"request_id": f"import:{batch.id}"},
        after={"status": batch.status, "counts": batch.counts},
    )  # fmt: skip


def dead(db: Session, ctx: JobContext) -> None:
    """Dead letter of a parse/load job: the batch becomes ``failed`` (visible, the job is retryable)."""
    batch = get_batch(db, _tenant(ctx), UUID(ctx.payload["batch_id"]), lock=True)
    if batch.status not in TERMINAL:
        _fail(
            db, batch, "failed", "Processing failed repeatedly; an admin can retry the job on the Jobs page."
        )


# ---- templates ----------------------------------------------------------------------------------


def _template(db: Session, tenant_id: UUID, template_id: UUID, *, lock: bool = False) -> MappingTemplate:
    stmt = select(MappingTemplate).where(
        MappingTemplate.tenant_id == tenant_id, MappingTemplate.id == template_id
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def get_template(db: Session, tenant_id: UUID, template_id: UUID) -> MappingTemplate:
    return _template(db, tenant_id, template_id)


def list_templates(
    db: Session, tenant_id: UUID, *, import_type: str | None, limit: int, cursor: str | None
) -> list[MappingTemplate]:
    stmt = select(MappingTemplate).where(MappingTemplate.tenant_id == tenant_id)
    if import_type is not None:
        stmt = stmt.where(MappingTemplate.import_type == import_type)
    return list(db.scalars(masterdata.keyset(stmt, MappingTemplate.id, cursor, limit)))


def _template_audit(t: MappingTemplate) -> dict[str, Any]:
    return {
        k: getattr(t, k) for k in ("name", "import_type", "header_signature", "mapping", "options", "version")
    }


def _check_template(
    import_type: str, headers: list[str], mapping: dict[str, Any], options: dict[str, Any]
) -> None:
    if import_type not in FIELDS:
        raise ValidationFailed(
            details=[
                {
                    "field": "import_type",
                    "code": "unsupported",
                    "message": "Templates for this type come with a later stage.",
                }
            ]
        )
    try:
        check_mapping(import_type, mapping, options, headers)
    except MappingInvalid as exc:
        raise ValidationFailed(details=exc.details) from None


def save_template(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    name: str,
    import_type: str,
    headers: list[str],
    mapping: dict[str, Any],
    options: dict[str, Any],
    meta: dict[str, Any],
    upsert: bool = False,
) -> MappingTemplate:
    """Create a template; with ``upsert`` an existing template of that name gets a new version."""
    _check_template(import_type, headers, mapping, options)
    existing = db.scalar(
        select(MappingTemplate)
        .where(MappingTemplate.tenant_id == tenant_id, MappingTemplate.name == name)
        .with_for_update()
    )
    if existing is not None:
        if not upsert:
            raise Conflict("A mapping template with this name already exists.")
        if existing.import_type != import_type:
            raise Conflict("A mapping template with this name exists for another import type.")
        return _new_version(db, existing, user_id, headers, mapping, options, meta)
    now = clock.now()
    row = MappingTemplate(
        tenant_id=tenant_id, id=new_id(), name=name, import_type=import_type,
        header_signature=n.header_signature(headers), mapping=mapping, options=options, version=1,
        created_by=user_id, created_at=now, updated_at=now,
    )  # fmt: skip
    db.add(row)
    db.flush()
    audit.record(
        db, tenant_id=tenant_id, actor=Actor("user", user_id), action=Action.MAPPING_TEMPLATE_CREATED,
        entity_type="mapping_template", entity_id=row.id, after=_template_audit(row), meta=meta,
    )  # fmt: skip
    return row


def _new_version(
    db: Session,
    row: MappingTemplate,
    user_id: UUID,
    headers: list[str],
    mapping: dict[str, Any],
    options: dict[str, Any],
    meta: dict[str, Any],
) -> MappingTemplate:
    before = _template_audit(row)
    row.header_signature, row.mapping, row.options = n.header_signature(headers), mapping, options
    row.version += 1
    row.updated_by, row.updated_at = user_id, clock.now()
    db.flush()
    audit.record(
        db, tenant_id=row.tenant_id, actor=Actor("user", user_id), action=Action.MAPPING_TEMPLATE_UPDATED,
        entity_type="mapping_template", entity_id=row.id, before=before, after=_template_audit(row), meta=meta,
    )  # fmt: skip
    return row


def update_template(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    template_id: UUID,
    expected_version: int,
    headers: list[str],
    mapping: dict[str, Any],
    options: dict[str, Any],
    meta: dict[str, Any],
) -> MappingTemplate:
    row = _template(db, tenant_id, template_id, lock=True)
    if row.version != expected_version:
        raise PreconditionFailed()
    _check_template(row.import_type, headers, mapping, options)
    return _new_version(db, row, user_id, headers, mapping, options, meta)


def delete_template(
    db: Session, *, tenant_id: UUID, user_id: UUID, template_id: UUID, meta: dict[str, Any]
) -> None:
    """Batches keep their own copy of the mapping (lineage); their template reference becomes NULL."""
    row = _template(db, tenant_id, template_id, lock=True)
    before = _template_audit(row)
    db.delete(row)
    db.flush()
    audit.record(
        db, tenant_id=tenant_id, actor=Actor("user", user_id), action=Action.MAPPING_TEMPLATE_DELETED,
        entity_type="mapping_template", entity_id=template_id, before=before, meta=meta,
    )  # fmt: skip


# ---- reads for the API --------------------------------------------------------------------------


def preview(db: Session, batch: ImportBatch, limit: int = 20) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(ImportStagedRow)
            .where(ImportStagedRow.import_batch_id == batch.id)
            .order_by(ImportStagedRow.row_number)
            .limit(limit)
        )
    )
    numbers = [r.row_number for r in rows]
    issues: dict[int, list[ImportRowError]] = {}
    for e in db.scalars(
        select(ImportRowError).where(
            ImportRowError.tenant_id == batch.tenant_id,
            ImportRowError.import_batch_id == batch.id,
            ImportRowError.row_number.in_(numbers),
        )
    ):
        issues.setdefault(e.row_number or 0, []).append(e)
    template = None
    if batch.mapping_template_id is not None:
        t = db.get(MappingTemplate, batch.mapping_template_id)
        template = (
            None if t is None else {"id": t.id, "name": t.name, "version": batch.mapping_template_version}
        )
    return {
        "headers": batch.headers or [],
        "suggested_mapping": suggest_mapping(batch.import_type, batch.headers or []) if batch.headers else {},
        "template": template,
        "fields": [
            {"name": f.name, "kind": f.kind, "required": f.required} for f in FIELDS[batch.import_type]
        ],
        "rows": [
            {
                "row_number": r.row_number,
                "raw": {k: v for k, v in r.raw.items() if not k.startswith("\x1f")},
                "normalized": r.normalized,
                "status": r.status,
                "issues": [
                    {"column": e.column_name, "code": e.code, "message": e.message, "severity": e.severity}
                    for e in issues.get(r.row_number, [])
                ],
            }
            for r in rows
        ],
    }


def list_errors(
    db: Session, tenant_id: UUID, batch_id: UUID, *, limit: int | None, cursor: str | None
) -> list[ImportRowError]:
    get_batch(db, tenant_id, batch_id)
    stmt = select(ImportRowError).where(
        ImportRowError.tenant_id == tenant_id, ImportRowError.import_batch_id == batch_id
    )
    if limit is None:  # CSV export: every error, in file order
        return list(db.scalars(stmt.order_by(ImportRowError.row_number.nulls_first(), ImportRowError.id)))
    return list(db.scalars(masterdata.keyset(stmt, ImportRowError.id, cursor, limit, newest_first=False)))


def job_progress(batch: ImportBatch) -> int:
    if batch.status in TERMINAL:
        return 100
    if batch.status != "loading":
        return 0
    total = batch.counts.get("valid", 0) or 1
    done = sum(batch.counts.get(k, 0) for k in ("inserted", "unchanged", "changed"))
    return int(min(99, done * 100 // total))


def counts_of(batch: ImportBatch) -> dict[str, int]:
    keys = ("rows_total", "valid", "rejected", "duplicates", "warnings", *LOADED_COUNTS[:3], "source_changed")
    return {k: int(batch.counts.get(k, 0)) for k in keys}
