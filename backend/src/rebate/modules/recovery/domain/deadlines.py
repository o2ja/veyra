"""Deadlines (BUSINESS_LOGIC.md 12). Pure calendar-day arithmetic, no weekend or holiday rolling.

"Today" comes from an injected UTC instant and the tenant's IANA zone; this module never reads a clock.
``None`` results mean ``deadline_unknown``.
"""

from calendar import monthrange
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from rebate_engine.deadlines import deadline, rebate_deadline

SPA_BASES = frozenset({"invoice_date", "ship_date", "month_end"})


class RejectedWindowBasis(ValueError):
    """`agreement_end` is not a basis; `period_end` is rebates-only (section 12)."""


def spa_claim_deadline(basis: str | None, claim_date: date | None, days: int | None) -> date | None:
    """``claim_date`` is the line's resolved claim date (invoice date, or ship date with fallback)."""
    if basis is None or claim_date is None or days is None:
        return None
    if basis not in SPA_BASES:
        raise RejectedWindowBasis(basis)
    start = (
        claim_date.replace(day=monthrange(claim_date.year, claim_date.month)[1])
        if basis == "month_end"
        else claim_date
    )
    return deadline(start, days)


def rebate_claim_deadline(period_end: date | None, days: int | None) -> date | None:
    if period_end is None or days is None:
        return None
    return rebate_deadline(period_end, days)


def dispute_deadline(base_date: date | None, dispute_window_days: int | None) -> date | None:
    """Remittance document date (or submission date per setting) + dispute window."""
    if base_date is None or dispute_window_days is None:
        return None
    return deadline(base_date, dispute_window_days)


def tenant_today(now_utc: datetime, zone: str) -> date:
    if now_utc.utcoffset() != UTC.utcoffset(None):
        raise ValueError("now_utc must be a UTC-aware datetime")
    return now_utc.astimezone(ZoneInfo(zone)).date()


def days_remaining(due: date | None, today: date) -> int | None:
    """0 on the deadline day, negative once past."""
    return None if due is None else (due - today).days


def is_outside_window(due: date | None, today: date) -> bool:
    """The deadline day itself is still inside the window (D3: eligible on 2026-05-30)."""
    return due is not None and today > due
