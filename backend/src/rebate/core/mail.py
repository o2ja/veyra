"""TEMPORARY in-process email sender (S1.6; PROJECT_STATUS.md K-9). Replaced by the job-based sender
in S2.1: each ``send_after_commit`` call site becomes ``jobs.enqueue("send_email", ...)``.

What is temporary, stated plainly:
* SMTP to Mailpit only. ``Settings`` refuses this backend outside APP_ENV local/ci.
* Messages are handed to a small thread pool **after** the request transaction commits, so the
  response never waits for SMTP (uniform timing, no enumeration through latency).
* Not durable and no retry: a crash or SMTP failure loses the message. The failure is logged with the
  template name only, never the address, link or exception text (which may contain the address).
"""

import smtplib
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from email.message import EmailMessage

import structlog

from rebate.core.config import Settings
from rebate.core.request import RequestContext

log = structlog.get_logger("rebate.mail")

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")
_pending: set[Future[None]] = set()


@dataclass(frozen=True)
class Mail:
    template: str  # e.g. "verify_email"; the only thing ever logged about a message
    to: str
    subject: str
    body: str


def _deliver(settings: Settings, mail: Mail) -> None:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg.set_content(mail.body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("email.send_failed", template=mail.template, error=type(exc).__name__)
        return
    log.info("email.sent", template=mail.template)


def _submit(fn: Callable[[], None]) -> None:
    future = _pool.submit(fn)
    _pending.add(future)
    future.add_done_callback(_pending.discard)


def send_after_commit(context: RequestContext, settings: Settings, mail: Mail) -> None:
    context.after_commit(lambda: _submit(lambda: _deliver(settings, mail)))


def background_after_commit(context: RequestContext, fn: Callable[[], None]) -> None:
    """Run ``fn`` off the response path once the request has committed (same temporary pool).

    Used where the *amount of work* would otherwise reveal whether an account exists (forgot
    password: S1 timing test). Replaced by a job in S2 together with the mailer (K-9)."""

    def guarded() -> None:
        try:
            fn()
        except Exception as exc:  # never kill the pool; never log details that may hold PII
            log.error("background.failed", error=type(exc).__name__)

    context.after_commit(lambda: _submit(guarded))


def flush(timeout: float = 10.0) -> None:
    """Wait until the pool is idle (tests and shutdown); background work may queue more mail."""
    deadline = time.monotonic() + timeout
    while _pending and time.monotonic() < deadline:
        wait(list(_pending), timeout=max(0.0, deadline - time.monotonic()))
