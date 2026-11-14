"""Deadline reminders (BUSINESS_LOGIC.md 12, D2): 14/7/3 days before, deduped, digested."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from rebate_engine.deadlines import reminder_dates

OFFSETS = (14, 7, 3)
KIND = "deadline_reminder"


@dataclass(frozen=True)
class Reminder:
    case_id: str
    offset: int
    due: date
    dedupe_key: str


def reminder_due(case_id: str, due: date | None, today: date) -> Reminder | None:
    """The one reminder a sweep on ``today`` creates, or ``None``.

    Of the offsets whose date has been reached only the most urgent is created, so a late-created case
    or a missed sweep does not flood. Idempotent through ``dedupe_key`` (case, kind, offset). Nothing for
    an unknown or past deadline; the deadline day itself still reminds.
    """
    if due is None or today > due:
        return None
    reached = [o for o, d in zip(OFFSETS, reminder_dates(due, OFFSETS), strict=True) if d <= today]
    if not reached:
        return None
    o = min(reached)
    return Reminder(case_id, o, due, f"{case_id}:{KIND}:{o}")


@dataclass(frozen=True)
class Digest:
    recipient_id: str
    reminders: tuple[Reminder, ...]  # soonest deadline first, then case id


def group_digest(entries: list[tuple[str, Reminder]]) -> tuple[Digest, ...]:
    """One digest per recipient (ordered by id); entries with the same dedupe key collapse."""
    by_user: dict[str, dict[str, Reminder]] = defaultdict(dict)
    for recipient, r in entries:
        by_user[recipient].setdefault(r.dedupe_key, r)
    return tuple(
        Digest(u, tuple(sorted(by_user[u].values(), key=lambda r: (r.due, r.case_id, r.offset))))
        for u in sorted(by_user)
    )
