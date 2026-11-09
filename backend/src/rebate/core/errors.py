"""Typed domain errors with stable codes, and the API error envelope (API_SPECIFICATION.md §1.1)."""

from typing import Any


class DomainError(Exception):
    code: str = "bad_request"
    status_code: int = 400
    message: str = "The request could not be processed."

    def __init__(self, message: str | None = None, details: list[dict[str, Any]] | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or []


class Unavailable(DomainError):
    code = "unavailable"
    status_code = 503
    message = "A required dependency is unavailable."


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
