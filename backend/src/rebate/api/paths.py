"""API path constants and the route template used in logs and audit entries."""

from fastapi import Request

API_PREFIX = "/api/v1"


def route_template(request: Request) -> str:
    """``/api/v1/users/{user_id}``, never the raw path (which may carry ids; it must never carry tokens).
    FastAPI includes routers lazily, so ``route.path`` is relative to the include prefix; every router
    here is included under API_PREFIX."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return "unmatched"
    return f"{API_PREFIX}{path}" if request.url.path.startswith(API_PREFIX) else str(path)
