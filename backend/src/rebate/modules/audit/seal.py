"""Audit sealing and verification (ADR-026, SECURITY.md audit section).

Canonical entry (the exact bytes that are hashed): UTF-8 JSON, keys sorted, separators ``,`` and ``:``, no
NaN, of the object ``{id, tenant_id, occurred_at, actor_type, actor_user_id, action, entity_type, entity_id,
before, after, request_id, ip, user_agent, metadata}``. Ids and ``ip`` are their text form, ``occurred_at`` is
UTC ``YYYY-MM-DDTHH:MM:SS.ffffffZ``, absent values are JSON ``null``.

Chain: ``h0 = prev seal's seal_hash`` (empty string for a tenant's first seal) and, for each entry in ``id``
order, ``h = SHA-256(h || canonical_entry)`` as lowercase hex. The seal covers this tenant's entries
``from_audit_id`` .. ``to_audit_id`` (by id) and stores ``seal_hash`` = the last ``h``. One running chain
therefore links every entry of the tenant, across seals.

Only entries older than ``SETTLE`` are sealed, so a transaction that allocated a lower id but commits later
cannot land inside a sealed range (API statements time out after 30 s; ``SETTLE`` is 5 min).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

SETTLE = timedelta(minutes=5)
CHUNK = 1000
_COLUMNS = (
    "id, tenant_id::text AS tenant_id, occurred_at, actor_type, actor_user_id::text AS actor_user_id, "
    "action, entity_type, entity_id::text AS entity_id, before, after, request_id, ip::text AS ip, "
    "user_agent, metadata"
)


def canonical(row: Any) -> bytes:
    entry = dict(row._mapping)
    entry["occurred_at"] = entry["occurred_at"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return json.dumps(
        entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def chain(prev_hash: str | None, rows: list[Any]) -> str:
    h = prev_hash or ""
    for row in rows:
        h = hashlib.sha256(h.encode() + canonical(row)).hexdigest()
    return h


def _entries(db: Session, tenant_id: UUID, low: int, high: int | None, limit: int | None) -> list[Any]:
    sql = f"SELECT {_COLUMNS} FROM audit_log WHERE tenant_id = :t AND id >= :lo"  # noqa: S608  # nosec B608
    sql += " AND id <= :hi" if high is not None else ""
    sql += " ORDER BY id" + (" LIMIT :n" if limit is not None else "")
    return list(db.execute(text(sql), {"t": tenant_id, "lo": low, "hi": high, "n": limit}).all())


def _last_seal(db: Session, tenant_id: UUID) -> tuple[int, str] | None:
    row = db.execute(
        text("SELECT to_audit_id, seal_hash FROM audit_seals WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"),
        {"t": tenant_id},
    ).first()
    return None if row is None else (row.to_audit_id, row.seal_hash)


def seal_tenant(db: Session, tenant_id: UUID, now: datetime, max_chunks: int = 50) -> bool:
    """Append up to ``max_chunks`` seals of ``CHUNK`` entries each. ``db`` has ``app.tenant_id`` set. Idempotent
    (nothing unsealed = no-op) and resumable (the last seal is the only state). Returns True if more is due."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"audit_seal:{tenant_id}"}
    )
    cutoff = now - SETTLE
    for _ in range(max_chunks):
        last = _last_seal(db, tenant_id)
        rows = _entries(db, tenant_id, 0 if last is None else last[0] + 1, None, CHUNK)
        for i, row in enumerate(rows):
            if row.occurred_at > cutoff:
                rows = rows[:i]
                break
        if not rows:
            return False
        prev = None if last is None else last[1]
        db.execute(
            text(
                "INSERT INTO audit_seals (tenant_id, from_audit_id, to_audit_id, prev_hash, seal_hash, "
                "sealed_at) VALUES (:t, :f, :to, :p, :h, :now)"
            ),
            {
                "t": tenant_id,
                "f": rows[0].id,
                "to": rows[-1].id,
                "p": prev,
                "h": chain(prev, rows),
                "now": now,
            },
        )
    return True


@dataclass(frozen=True)
class Verification:
    ok: bool
    seals: int  # seals verified before the first break (all of them when ok)
    head_hash: str | None
    unsealed_entries: int
    failure: str | None = None
    failed_seal_id: int | None = None


def verify_tenant(db: Session, tenant_id: UUID) -> Verification:
    """Recompute the tenant's chain; stop at the first broken seal. ``db`` has ``app.tenant_id`` set.
    A deleted *last* seal is not detectable from the database alone: compare ``head_hash`` with a copy kept
    elsewhere (runbook)."""
    seals = db.execute(
        text(
            "SELECT id, from_audit_id, to_audit_id, prev_hash, seal_hash FROM audit_seals "
            "WHERE tenant_id = :t ORDER BY id"
        ),
        {"t": tenant_id},
    ).all()
    prev_hash: str | None = None
    prev_to = -1
    for n, s in enumerate(seals):
        why = None
        rows: list[Any] = []
        if s.prev_hash != prev_hash:
            why = "prev_hash does not match the previous seal (a seal was deleted or altered)"
        elif s.from_audit_id <= prev_to:
            why = "range overlaps the previous seal"
        else:
            rows = _entries(db, tenant_id, s.from_audit_id, s.to_audit_id, None)
            if not rows or rows[0].id != s.from_audit_id or rows[-1].id != s.to_audit_id:
                why = "a sealed audit entry is missing"
            elif chain(s.prev_hash, rows) != s.seal_hash:
                why = "sealed entries no longer match the seal (an audit entry was altered)"
        if why:
            where = f"seal {s.id} (entries {s.from_audit_id}..{s.to_audit_id}): {why}"
            return Verification(False, n, prev_hash, 0, where, s.id)
        prev_hash, prev_to = s.seal_hash, s.to_audit_id
    unsealed = db.execute(
        text("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND id > :p"),
        {"t": tenant_id, "p": prev_to},
    ).scalar_one()
    return Verification(True, len(seals), prev_hash, unsealed)


# Tables whose age is unambiguous (own creation timestamp). Which date makes a *financial* record "past
# retention" (document date, claim settlement, ...) is not specified: see the S11.1 notes (owner decision).
RETENTION_TABLES = (
    ("audit_log", "occurred_at"),
    ("file_objects", "created_at"),
    ("import_batches", "created_at"),
    ("calculation_runs", "created_at"),
)


def retention_report(db: Session, tenant_id: UUID, now: datetime) -> dict[str, Any]:
    """Read-only dry run: what is older than the workspace's ``retention_years``. Deletes nothing."""
    years = db.execute(
        text("SELECT retention_years FROM tenant_settings WHERE tenant_id = :t"), {"t": tenant_id}
    ).scalar_one()
    cutoff = db.execute(
        text("SELECT CAST(:now AS timestamptz) - make_interval(years => :y)"), {"now": now, "y": years}
    ).scalar_one()
    tables: dict[str, Any] = {}
    for table, column in RETENTION_TABLES:
        sql = (
            f"SELECT count(*) AS total, count(*) FILTER (WHERE {column} < :c) AS past, min({column}) AS oldest "  # noqa: S608  # nosec B608 (names are constants)
            f"FROM {table} WHERE tenant_id = :t"
        )
        r = db.execute(text(sql), {"c": cutoff, "t": tenant_id}).one()
        tables[table] = {
            "total": r.total,
            "past_retention": r.past,
            "oldest": r.oldest and r.oldest.isoformat(),
        }
    last = _last_seal(db, tenant_id)
    tables["audit_log"]["past_retention_and_sealed"] = db.execute(
        text("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND occurred_at < :c AND id <= :s"),
        {"t": tenant_id, "c": cutoff, "s": -1 if last is None else last[0]},
    ).scalar_one()
    return {
        "tenant_id": str(tenant_id),
        "retention_years": years,
        "cutoff": cutoff.isoformat(),
        "dry_run": True,
        "tables": tables,
    }
