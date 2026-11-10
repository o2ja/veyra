"""SMTP delivery, called from jobs only (S2 replaced the temporary in-process pool, K-9).

``deliver`` raises on failure so the job is retried with backoff; after the last attempt the job is
visible as failed in the admin jobs view. Only the template name and exception class are logged: SMTP
errors can carry the recipient address.
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import structlog

from rebate.core.config import Settings

log = structlog.get_logger("rebate.mail")


@dataclass(frozen=True)
class Mail:
    template: str  # e.g. "verify_email"; the only thing ever logged about a message
    to: str
    subject: str
    body: str


class MailDeliveryError(Exception):
    """Transient: the job runner retries."""


def deliver(settings: Settings, mail: Mail) -> None:
    try:
        msg = EmailMessage()
        msg["From"] = settings.mail_from
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg.set_content(mail.body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user is not None and settings.smtp_password is not None:
                smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            smtp.send_message(msg)
    except Exception as exc:
        log.warning("email.send_failed", template=mail.template, error=type(exc).__name__)
        raise MailDeliveryError(type(exc).__name__) from None
    log.info("email.sent", template=mail.template)
