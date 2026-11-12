"""Case lifecycle (BUSINESS_LOGIC.md 12.1): transitions, approver-only write-off, four-eyes, recovery."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class CaseStatus(StrEnum):
    OPEN = "open"
    TO_RECLAIM = "to_reclaim"
    SUBMITTED = "submitted"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"
    CLOSED_NO_ACTION = "closed_no_action"


S = CaseStatus
ALLOWED: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.OPEN: frozenset({S.TO_RECLAIM, S.CLOSED_NO_ACTION, S.WRITTEN_OFF}),
    S.TO_RECLAIM: frozenset({S.SUBMITTED, S.WRITTEN_OFF}),
    S.SUBMITTED: frozenset({S.RECOVERED, S.TO_RECLAIM, S.WRITTEN_OFF}),
    S.RECOVERED: frozenset(),
    S.WRITTEN_OFF: frozenset(),
    S.CLOSED_NO_ACTION: frozenset(),
}


class TransitionError(Exception):
    code = "invalid_transition"


class IllegalTransition(TransitionError):
    code = "illegal_transition"


class ApproverRequired(TransitionError):
    code = "approver_required"


class WriteOffNotProposed(TransitionError):
    code = "write_off_not_proposed"


class FourEyesViolation(TransitionError):
    code = "four_eyes_violation"


@dataclass(frozen=True)
class Actor:
    user_id: str
    is_approver: bool


def check_transition(
    current: CaseStatus, target: CaseStatus, actor: Actor, *, write_off_proposed_by: str | None = None
) -> None:
    """Raise unless ``actor`` may move the case. A write-off needs an approver other than its proposer."""
    if target not in ALLOWED[current]:
        raise IllegalTransition(f"{current.value} -> {target.value}")
    if target is S.WRITTEN_OFF:
        if not actor.is_approver:
            raise ApproverRequired("write-off requires an approver")
        if write_off_proposed_by is None:
            raise WriteOffNotProposed("write-off must be proposed first")
        if write_off_proposed_by == actor.user_id:
            raise FourEyesViolation("the approver must differ from the proposer")


def apply_recovery(
    status: CaseStatus, case_amount: Decimal, recovered_so_far: Decimal, payment: Decimal
) -> tuple[Decimal, CaseStatus]:
    """Partial recovery keeps ``submitted``; reaching the case amount makes it ``recovered``."""
    if status is not S.SUBMITTED:
        raise IllegalTransition(f"recovery on {status.value} case")
    if payment <= 0:
        raise ValueError("recovery payment must be positive")
    total = recovered_so_far + payment
    return total, (S.RECOVERED if total >= case_amount else S.SUBMITTED)
