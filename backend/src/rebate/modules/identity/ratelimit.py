"""Fixed-window counters in Postgres (`rate_limit_counters`, S1 D-4): correct across API workers.

Keys never contain raw emails (keyed HMAC) or tokens. ``RateLimited`` commits (the hit counts even
though the request fails). Expired rows are pruned on write.
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.errors import RateLimited
from rebate.modules.identity.models import RateLimitCounter

_UPSERT = text(
    """
    INSERT INTO rate_limit_counters AS c (key, window_start, count, expires_at)
    VALUES (:key, :now, 1, :expires)
    ON CONFLICT (key) DO UPDATE SET
        count = CASE WHEN c.expires_at > :now THEN c.count + 1 ELSE 1 END,
        window_start = CASE WHEN c.expires_at > :now THEN c.window_start ELSE :now END,
        expires_at = CASE WHEN c.expires_at > :now THEN c.expires_at ELSE :expires END
    RETURNING count AS n, expires_at
    """
)

_GET = text(
    "SELECT count AS n, expires_at FROM rate_limit_counters WHERE key = :k AND expires_at > :now FOR UPDATE"
)

_PUT = text(
    "INSERT INTO rate_limit_counters (key, window_start, count, expires_at) VALUES (:k, :now, :c, :e) "
    "ON CONFLICT (key) DO UPDATE SET count = :c, expires_at = :e, window_start = :now"
)


def hit(db: Session, key: str, *, limit: int, window_seconds: int) -> int:
    """Count one event; raise ``RateLimited`` (with Retry-After) once ``limit`` is exceeded."""
    now = clock.now()
    db.execute(delete(RateLimitCounter).where(RateLimitCounter.expires_at < now - timedelta(minutes=5)))
    params = {"key": key[:200], "now": now, "expires": now + timedelta(seconds=window_seconds)}
    row = db.execute(_UPSERT, params).mappings().one()
    count = int(row["n"])
    if count > limit:
        retry = max(1, math.ceil((row["expires_at"] - now).total_seconds()))
        raise RateLimited(headers={"Retry-After": str(retry)})
    return count


def get(db: Session, key: str) -> tuple[int, datetime] | None:
    """Current (count, expires_at) of a live counter, or None."""
    row = db.execute(_GET, {"k": key, "now": clock.now()}).mappings().one_or_none()
    return None if row is None else (int(row["n"]), row["expires_at"])


def put(db: Session, key: str, count: int, expires_at: datetime) -> None:
    db.execute(_PUT, {"k": key, "now": clock.now(), "c": count, "e": expires_at})
