"""Deadlines (BUSINESS_LOGIC.md §12): pure calendar-day addition, no weekend or holiday rolling."""

from collections.abc import Iterable
from datetime import date, timedelta


def deadline(basis_date: date, days: int) -> date:
    if days < 0:
        raise ValueError("window days must be >= 0")
    return basis_date + timedelta(days=days)


def reminder_dates(due: date, days_before: Iterable[int]) -> tuple[date, ...]:
    """Notification dates `days_before` the deadline, in the given order."""
    return tuple(due - timedelta(days=d) for d in days_before)
