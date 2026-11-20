"""Typed domain errors with stable codes, and the API error envelope (API_SPECIFICATION.md §1.1).

``commit = True`` marks failures whose bookkeeping (failed-login counters, lockouts, rate-limit hits,
audit rows) must survive the error response: the request transaction commits, then the envelope is
returned (S1 kickoff review finding 3). Everything else rolls back.
"""

from typing import Any, ClassVar


class DomainError(Exception):
    code: str = "bad_request"
    status_code: int = 400
    message: str = "The request could not be processed."
    commit: ClassVar[bool] = False

    def __init__(
        self,
        message: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or []
        self.headers = headers or {}


class Unavailable(DomainError):
    code = "unavailable"
    status_code = 503
    message = "A required dependency is unavailable."


class Unauthenticated(DomainError):
    code = "unauthenticated"
    status_code = 401
    message = "Sign in to continue."


class InvalidCredentials(Unauthenticated):
    """Same message for unknown email, wrong password, disabled or unverified account."""

    message = "The email or password is incorrect."
    commit = True


class SessionExpired(DomainError):
    code = "session_expired"
    status_code = 401
    message = "Your session has expired. Sign in again."


class MfaRequired(DomainError):
    code = "mfa_required"
    status_code = 401
    message = "Enter the code from your authenticator app."


class InvalidMfaCode(MfaRequired):
    message = "The code is not valid. Try again."
    commit = True


class ReauthenticationRequired(DomainError):
    code = "reauthentication_required"
    status_code = 401
    message = "Confirm your password to continue."


class Forbidden(DomainError):
    code = "forbidden"
    status_code = 403
    message = "You do not have permission to do this."


class TenantSuspended(Forbidden):
    code = "tenant_suspended"
    message = "Account suspended. Please contact support."


class CsrfFailed(DomainError):
    code = "csrf_failed"
    status_code = 403
    message = "The request could not be verified. Reload the page and try again."


class MfaEnrollmentRequired(DomainError):
    code = "mfa_enrollment_required"
    status_code = 403
    message = "Set up two-factor authentication to continue."


class FourEyesViolation(DomainError):
    code = "four_eyes_violation"
    status_code = 403
    message = "A different person must approve this."


class NotFound(DomainError):
    code = "not_found"
    status_code = 404
    message = "Not found."


class Conflict(DomainError):
    code = "conflict"
    status_code = 409
    message = "The request conflicts with the current state."


class InvalidState(Conflict):
    code = "invalid_state"


class LinkExpired(DomainError):
    """Expired, used, revoked or unknown token: one generic answer (no enumeration)."""

    code = "link_expired"
    status_code = 410
    message = "This link is no longer valid. Ask for a new one."


class InvitationExpired(LinkExpired):
    code = "invitation_expired"
    message = "This invitation is no longer valid. Ask an admin to send a new one."


class PreconditionFailed(DomainError):
    code = "precondition_failed"
    status_code = 412
    message = "The resource was changed by someone else. Reload and try again."


class PasswordTooCommon(DomainError):
    code = "password_too_common"
    status_code = 422
    message = "This password is too common. Choose a longer or less predictable one."


class ValidationFailed(DomainError):
    code = "validation_error"
    status_code = 422
    message = "One or more fields are invalid."


class AccountLocked(DomainError):
    code = "account_locked"
    status_code = 423
    message = "Too many failed attempts. Try again later."
    commit = True


class RateLimited(DomainError):
    code = "rate_limited"
    status_code = 429
    message = "Too many requests. Try again later."
    commit = True


def error_body(
    code: str, message: str, request_id: str | None, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": request_id,
        }
    }
