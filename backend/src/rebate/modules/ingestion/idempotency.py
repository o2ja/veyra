"""``Idempotency-Key`` for ``POST /imports`` (API_SPECIFICATION.md §1: same key + same body -> original
response replayed; same key + different body -> 409 ``idempotency_conflict``; retained 24 h).

The table ``idempotency_keys`` (S1, audit module) had no user before S3. No shared helper exists yet, so
this module reads and writes it directly; a later stage that adds the second idempotent endpoint should
move this into a shared place (recorded in the S3 notes). Concurrent requests with the same key are
serialised by a transaction-scoped advisory lock.
"""

import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import Conflict, DomainError

RETENTION = timedelta(hours=24)


class IdempotencyKeyRequired(DomainError):
    code = "bad_request"
    status_code = 400
    message = "Send an Idempotency-Key header (a UUID) with this request."


class IdempotencyConflict(Conflict):
    code = "idempotency_conflict"
    message = "This Idempotency-Key was already used with a different request."


def parse_key(value: str | None) -> str:
    try:
        return str(UUID(value or ""))
    except ValueError:
        raise IdempotencyKeyRequired() from None


def _hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def replay(
    db: Session, tenant_id: UUID, key: str, endpoint: str, body: dict[str, Any]
) -> tuple[int, Any] | None:
    """Lock the key for this transaction; return the stored ``(status, body)`` if it was used before."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"idem:{tenant_id}:{endpoint}:{key}"},
    )
    row = db.execute(
        text(
            "SELECT request_hash, response_status, response_body FROM idempotency_keys "
            "WHERE tenant_id = :t AND key = :k AND endpoint = :e AND expires_at > :now"
        ),
        {"t": tenant_id, "k": key, "e": endpoint, "now": clock.now()},
    ).first()
    if row is None:
        return None
    if row.request_hash != _hash(body):
        raise IdempotencyConflict()
    return int(row.response_status), row.response_body


def store(
    db: Session, tenant_id: UUID, key: str, endpoint: str, body: dict[str, Any], status: int, response: Any
) -> None:
    now = clock.now()
    db.execute(
        text(
            "INSERT INTO idempotency_keys (tenant_id, key, endpoint, request_hash, response_status, response_body, "
            "created_at, expires_at) VALUES (:t, :k, :e, :h, :s, CAST(:b AS jsonb), :now, :exp) "
            "ON CONFLICT (tenant_id, key, endpoint) DO UPDATE SET request_hash = EXCLUDED.request_hash, "
            "response_status = EXCLUDED.response_status, response_body = EXCLUDED.response_body, "
            "created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at "
            "WHERE idempotency_keys.expires_at <= EXCLUDED.created_at"
        ),
        {
            "t": tenant_id,
            "k": key,
            "e": endpoint,
            "h": _hash(body),
            "s": status,
            "b": json.dumps(response),
            "now": now,
            "exp": now + RETENTION,
        },
    )
