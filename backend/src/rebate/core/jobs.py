"""Postgres job queue (ADR-005 fallback: Procrastinate cannot enqueue inside our SQLAlchemy/psycopg 3
transaction; spike recorded in ADR-005). ARCHITECTURE.md §6 rules, as implemented here:

* ``enqueue()`` inserts into ``jobs`` with the caller's session: the job exists iff the change commits.
* Payloads carry ids only (no secrets, tokens or email addresses; DB CHECK caps the size).
* Tenant-scoped handlers run in ``transaction(engine, job.tenant_id)``; a tenant-scoped job without a
  tenant fails permanently before the handler is called.
* The handler's effects and the job's ``succeeded`` mark commit in the same transaction.
* Retries: bounded (``max_attempts``), exponential backoff with jitter; ``PermanentError`` fails at once.
  Terminal state ``failed`` (dead letter) is visible in the admin API; ``on_dead`` hooks run then.
* A crashed worker's job is reclaimed when its lease (``locked_until``) expires.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import Engine, ForeignKey, Index, Integer, Text, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import DateTime

from rebate.core import clock
from rebate.core.config import Settings
from rebate.core.db import Base, transaction
from rebate.core.errors import DomainError
from rebate.core.ids import new_id

log = structlog.get_logger("rebate.jobs")
TS = DateTime(timezone=True)

LEASE = timedelta(minutes=10)
BASE_BACKOFF_SECONDS = 15
MAX_BACKOFF_SECONDS = 3600


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_queued_run_at", "run_at", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_running_locked_until", "locked_until", postgresql_where=text("status = 'running'")),
        Index("ix_jobs_tenant_id_status_created_at", "tenant_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.id", name="fk_jobs_tenants"))
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5")
    run_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_jobs_users"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class PermanentError(DomainError):
    """Retrying cannot help (bad input, missing record, missing tenant context): fail now."""

    code = "job_failed"


@dataclass(frozen=True)
class JobContext:
    settings: Settings
    id: UUID
    tenant_id: UUID | None
    payload: dict[str, Any]
    attempt: int
    created_by: UUID | None


Handler = Callable[[Session, JobContext], None]


@dataclass(frozen=True)
class JobType:
    kind: str
    handler: Handler
    tenant_scoped: bool
    max_attempts: int
    on_dead: Callable[[Session, JobContext], None] | None = field(default=None)


REGISTRY: dict[str, JobType] = {}


def job(
    kind: str,
    *,
    tenant_scoped: bool,
    max_attempts: int = 5,
    on_dead: Callable[[Session, JobContext], None] | None = None,
) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if kind in REGISTRY:
            raise ValueError(f"job kind {kind!r} registered twice")
        REGISTRY[kind] = JobType(kind, fn, tenant_scoped, max_attempts, on_dead)
        return fn

    return register


def enqueue(
    db: Session,
    kind: str,
    *,
    tenant_id: UUID | None,
    payload: dict[str, str],
    created_by: UUID | None = None,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> UUID | None:
    """Insert a job in the caller's transaction. With ``dedupe_key``, a second enqueue is a no-op
    (returns None). Payload values are ids (strings) only."""
    jt = REGISTRY.get(kind)
    if jt is None:
        raise ValueError(f"unknown job kind {kind!r}")
    if jt.tenant_scoped and tenant_id is None:
        raise ValueError(f"job {kind!r} is tenant-scoped; tenant_id is required")
    job_id = new_id()
    now = clock.now()
    stmt = (
        insert(Job)
        .values(
            id=job_id,
            tenant_id=tenant_id,
            kind=kind,
            payload=payload,
            max_attempts=jt.max_attempts,
            run_at=run_at or now,
            dedupe_key=dedupe_key,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Job.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def backoff(attempt: int) -> timedelta:
    seconds = min(BASE_BACKOFF_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds * random.uniform(0.8, 1.2))  # noqa: S311  # nosec B311 (retry jitter, not security)


def _safe_error(exc: BaseException) -> str:
    """Never store exception text that may hold PII: class name, plus our own domain messages."""
    if isinstance(exc, DomainError):
        return f"{type(exc).__name__}: {exc.message}"[:500]
    return type(exc).__name__[:500]


def _claim(engine: Engine) -> Job | None:
    now = clock.now()
    with transaction(engine) as db:
        row = db.scalar(
            select(Job)
            .where(
                ((Job.status == "queued") & (Job.run_at <= now))
                | ((Job.status == "running") & (Job.locked_until < now))
            )
            .order_by(Job.run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        row.status, row.attempts = "running", row.attempts + 1
        row.locked_until, row.updated_at = now + LEASE, now
        db.flush()
        db.expunge(row)
        return row


def _finish(db: Session, job_id: UUID, status: str, error: str | None = None) -> None:
    now = clock.now()
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=status, finished_at=now, updated_at=now, locked_until=None, last_error=error)
    )


def run_once(engine: Engine, settings: Settings) -> bool:
    """Claim and run one due job. Returns False when nothing was due."""
    claimed = _claim(engine)
    if claimed is None:
        return False
    ctx = JobContext(
        settings, claimed.id, claimed.tenant_id, dict(claimed.payload), claimed.attempts, claimed.created_by
    )
    jt = REGISTRY.get(claimed.kind)
    structlog.contextvars.bind_contextvars(job_id=str(claimed.id), job_kind=claimed.kind)
    try:
        if jt is None:
            raise PermanentError(f"no handler registered for {claimed.kind!r}")
        if jt.tenant_scoped and claimed.tenant_id is None:
            raise PermanentError("tenant-scoped job without tenant context")
        with transaction(engine, claimed.tenant_id if jt.tenant_scoped else None) as db:
            jt.handler(db, ctx)
            _finish(db, claimed.id, "succeeded")
        log.info("job.succeeded", attempt=claimed.attempts)
    except Exception as exc:  # the job outcome is recorded; the worker keeps running
        _record_failure(engine, claimed, jt, ctx, exc)
    finally:
        structlog.contextvars.unbind_contextvars("job_id", "job_kind")
    return True


def _record_failure(engine: Engine, job: Job, jt: JobType | None, ctx: JobContext, exc: Exception) -> None:
    error = _safe_error(exc)
    dead = isinstance(exc, PermanentError) or job.attempts >= job.max_attempts
    with transaction(engine) as db:
        if dead:
            _finish(db, job.id, "failed", error)
        else:
            now = clock.now()
            db.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(
                    status="queued",
                    run_at=now + backoff(job.attempts),
                    locked_until=None,
                    last_error=error,
                    updated_at=now,
                )
            )
    log.warning("job.failed" if dead else "job.retry", attempt=job.attempts, error=error)
    if dead and jt is not None and jt.on_dead is not None:
        try:
            tenant = job.tenant_id if jt.tenant_scoped else None
            with transaction(engine, tenant) as db:
                jt.on_dead(db, ctx)
        except Exception as hook_exc:
            log.error("job.on_dead_failed", error=type(hook_exc).__name__)


def retry(db: Session, job_id: UUID) -> bool:
    """Admin retry of a failed job: back to queued with a fresh attempt budget."""
    now = clock.now()
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "failed")
        .values(status="queued", attempts=0, run_at=now, finished_at=None, last_error=None, updated_at=now)
        .returning(Job.id)
    )
    return result.first() is not None
