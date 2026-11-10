"""identity module: users, credentials, MFA, sessions, rate limits, auth flows. Public API below."""

from rebate.modules.identity import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.identity.principal import resolve as resolve_principal
from rebate.modules.identity.router import router
from rebate.modules.identity.service import actor_labels, admin_emails
from rebate.modules.identity.sessions import CSRF_COOKIE, SESSION_COOKIE, csrf_for

__all__ = [
    "CSRF_COOKIE",
    "SESSION_COOKIE",
    "actor_labels",
    "admin_emails",
    "csrf_for",
    "resolve_principal",
    "router",
]
