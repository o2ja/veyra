"""The only source of "now" for application code (ARCHITECTURE.md §13). Tests freeze it."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime


def _system_now() -> datetime:
    return datetime.now(UTC)


_now: Callable[[], datetime] = _system_now


def now() -> datetime:
    return _now()


@contextmanager
def frozen(at: datetime) -> Iterator[None]:
    if at.tzinfo is None:
        raise ValueError("frozen time must be timezone-aware")
    global _now
    previous, _now = _now, lambda: at
    try:
        yield
    finally:
        _now = previous
