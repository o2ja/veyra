"""Audit event catalogue (SECURITY.md §7) and the fields each entity type may record.

Only allow-listed keys reach ``before``/``after`` (S1 review finding 26): secrets such as password
hashes, MFA secrets, token or code hashes can never be written, even by mistake.
"""

from enum import StrEnum


class Action(StrEnum):
    AUTH_LOGIN_SUCCEEDED = "auth.login_succeeded"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOCKED = "auth.locked"
    AUTH_LOGOUT = "auth.logout"
    AUTH_MFA_ENABLED = "auth.mfa_enabled"
    AUTH_MFA_FAILED = "auth.mfa_failed"
    AUTH_RECOVERY_CODE_USED = "auth.recovery_code_used"
    AUTH_RECOVERY_CODES_REGENERATED = "auth.recovery_codes_regenerated"
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"  # noqa: S105  # nosec B105 (event name)
    AUTH_PASSWORD_RESET = "auth.password_reset"  # noqa: S105  # nosec B105
    AUTH_PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105  # nosec B105
    AUTH_EMAIL_CHANGE_REQUESTED = "auth.email_change_requested"
    AUTH_EMAIL_CHANGED = "auth.email_changed"
    AUTH_REAUTHENTICATED = "auth.reauthenticated"
    TENANT_CREATED = "tenant.created"
    TENANT_SWITCHED = "tenant.switched"
    SETTINGS_UPDATED = "settings.updated"
    MEMBERSHIP_CREATED = "membership.created"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_DEACTIVATED = "membership.deactivated"
    MEMBERSHIP_REACTIVATED = "membership.reactivated"
    INVITATION_CREATED = "invitation.created"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_ACCEPTED = "invitation.accepted"
    AUTHZ_DENIED = "authz.denied"


AUDITED_FIELDS: dict[str, frozenset[str]] = {
    "user": frozenset({"email", "full_name", "mfa_enabled"}),
    "tenant": frozenset({"name", "slug", "status"}),
    "settings": frozenset(
        {
            "business_timezone",
            "mfa_required",
            "four_eyes_required",
            "ai_processing_enabled",
            "retention_years",
        }
    ),
    "membership": frozenset({"user_id", "role", "status"}),
    "invitation": frozenset({"email", "role", "expires_at"}),
    "session": frozenset(),
    "permission": frozenset({"permission", "method", "route"}),
}
