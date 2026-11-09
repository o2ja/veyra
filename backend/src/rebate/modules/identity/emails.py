"""Plain-text email bodies. Links carry tokens in the URL fragment (never sent to a server or logged)."""

from rebate.core.config import Settings
from rebate.core.mail import Mail


def _link(settings: Settings, page: str, token: str) -> str:
    return f"{settings.public_origin}/{page}#token={token}"


def verify_email(settings: Settings, to: str, token: str) -> Mail:
    return Mail(
        "verify_email",
        to,
        "Confirm your email for Veyra Recover",
        "Confirm your email address to finish creating your Veyra Recover workspace:\n\n"
        f"{_link(settings, 'verify-email', token)}\n\n"
        f"The link works once and expires in {settings.email_verify_token_hours} hours. "
        "If you did not sign up, ignore this email.",
    )


def already_registered(settings: Settings, to: str) -> Mail:
    return Mail(
        "already_registered",
        to,
        "Someone tried to register with your email",
        "Someone tried to create a Veyra Recover workspace with this email address, which already has an "
        f"account. If it was you, sign in at {settings.public_origin}/login or reset your password there. "
        "If not, you can ignore this email; nothing was changed.",
    )


def password_reset(settings: Settings, to: str, token: str) -> Mail:
    return Mail(
        "password_reset",
        to,
        "Reset your Veyra Recover password",
        f"Reset your password: {_link(settings, 'reset-password', token)}\n\n"
        f"The link works once and expires in {settings.password_reset_token_minutes} minutes. "
        "If you did not ask for this, ignore this email; your password has not changed.",
    )


def email_change_confirm(settings: Settings, to: str, token: str) -> Mail:
    return Mail(
        "email_change_confirm",
        to,
        "Confirm your new email for Veyra Recover",
        f"Confirm this as your new sign-in email: {_link(settings, 'confirm-email', token)}\n\n"
        f"The link works once and expires in {settings.email_verify_token_hours} hours.",
    )


def email_change_in_use(settings: Settings, to: str) -> Mail:
    return Mail(
        "email_change_in_use",
        to,
        "Email change request for Veyra Recover",
        "Someone asked to use this address as the sign-in email of a Veyra Recover account, but it already "
        "belongs to an account. Nothing was changed.",
    )


def email_change_notice(settings: Settings, to: str) -> Mail:
    return Mail(
        "email_change_notice",
        to,
        "Your Veyra Recover email is being changed",
        "A change of the sign-in email for your Veyra Recover account was requested. If this was not you, "
        f"reset your password at {settings.public_origin}/login and contact your workspace admin.",
    )
