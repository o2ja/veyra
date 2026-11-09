"""Progressive, decaying lockout (SECURITY.md §1; S1 review findings 3, 4, 17).

Identical behaviour for real accounts (columns on ``users``) and unknown emails (counters keyed by an
HMAC of the email), so a 423 never reveals whether an account exists:

* ``lockout_threshold`` consecutive failures (password or MFA) → locked for
  ``base x 2^level`` minutes (capped), level + 1; the failing attempt itself gets 423.
* The level decays to 0 once the last lockout ended more than ``lockout_decay_hours`` ago.
* Failures reset only after **full** authentication (MFA passed), or by a password reset.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.config import Settings
from rebate.core.security import keyed_hash
from rebate.core.validation import email_key
from rebate.modules.identity import ratelimit
from rebate.modules.identity.models import User


@dataclass
class LockState:
    failed: int
    level: int
    locked_until: datetime | None


def _minutes(settings: Settings, level: int) -> int:
    return min(settings.lockout_base_minutes << min(level, 16), settings.lockout_max_minutes)


def _fail(settings: Settings, state: LockState) -> bool:
    """Apply one failure; return True if this failure locks the account."""
    now = clock.now()
    decayed = state.locked_until is not None and now - state.locked_until > timedelta(
        hours=settings.lockout_decay_hours
    )
    if decayed:
        state.level = 0
    state.failed += 1
    if state.failed < settings.lockout_threshold:
        return False
    state.locked_until = now + timedelta(minutes=_minutes(settings, state.level))
    state.level += 1
    state.failed = 0
    return True


def is_locked(state: LockState) -> bool:
    return state.locked_until is not None and state.locked_until > clock.now()


# ---- real accounts ------------------------------------------------------------------------------


def user_state(user: User) -> LockState:
    return LockState(user.failed_login_count, user.lockout_level, user.locked_until)


def user_failure(settings: Settings, user: User) -> bool:
    state = user_state(user)
    locked = _fail(settings, state)
    user.failed_login_count, user.lockout_level, user.locked_until = (
        state.failed,
        state.level,
        state.locked_until,
    )
    return locked


def user_success(user: User) -> None:
    user.failed_login_count = 0


def user_clear(user: User) -> None:
    user.failed_login_count, user.lockout_level, user.locked_until = 0, 0, None


# ---- unknown emails (no account): same algorithm over counters --------------------------------


def _keys(settings: Settings, email: str) -> tuple[str, str, str]:
    h = keyed_hash(settings.secret_key.get_secret_value(), "lockout", email_key(email))[:40]
    return f"lf:{h}", f"ll:{h}", f"lu:{h}"


def unknown_state(db: Session, settings: Settings, email: str) -> LockState:
    kf, kl, ku = _keys(settings, email)
    failed, level, locked = ratelimit.get(db, kf), ratelimit.get(db, kl), ratelimit.get(db, ku)
    return LockState(failed[0] if failed else 0, level[0] if level else 0, locked[1] if locked else None)


def unknown_failure(db: Session, settings: Settings, email: str) -> bool:
    state = unknown_state(db, settings, email)
    locked = _fail(settings, state)
    kf, kl, ku = _keys(settings, email)
    horizon = clock.now() + timedelta(hours=settings.lockout_decay_hours)
    ratelimit.put(db, kf, state.failed, horizon)
    if state.locked_until is not None:
        # level lives until the decay period after the lock ends; the lock key expires with the lock
        ratelimit.put(db, kl, state.level, state.locked_until + timedelta(hours=settings.lockout_decay_hours))
        ratelimit.put(db, ku, 1, state.locked_until)
    return locked
