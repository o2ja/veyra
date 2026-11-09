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


def send_after_commit(context: RequestContext, settings: Settings, mail: Mail) -> None:
    def submit() -> None:
        future = _pool.submit(_deliver, settings, mail)
        _pending.add(future)
        future.add_done_callback(_pending.discard)

    context.after_commit(submit)


def flush(timeout: float = 10.0) -> None:
    """Wait for queued messages (tests and shutdown)."""
    wait(list(_pending), timeout=timeout)
