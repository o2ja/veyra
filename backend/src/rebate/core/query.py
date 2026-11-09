"""Query-string rules (API_SPECIFICATION.md §1.2): unknown parameters are rejected with 400."""

from collections.abc import Callable

from fastapi import Request

from rebate.core.errors import DomainError


class UnknownQueryParameter(DomainError):
    code = "bad_request"
    status_code = 400
    message = "Unknown query parameter."


def only_query_params(*allowed: str) -> Callable[[Request], None]:
    allowed_set = frozenset(allowed)

    def dependency(request: Request) -> None:
        unknown = sorted(set(request.query_params.keys()) - allowed_set)
        if unknown:
            raise UnknownQueryParameter(
                details=[
                    {"field": name, "code": "unknown", "message": "Unknown parameter."} for name in unknown
                ]
            )

    return dependency
