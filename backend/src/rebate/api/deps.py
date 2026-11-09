"""Request dependencies. One request = one transaction (ARCHITECTURE.md §4)."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from rebate.core.db import transaction


def db_session(request: Request) -> Iterator[Session]:
    """Commits when the handler returns, rolls back if it raises. The tenant context is set here once
    sessions exist (S1); until then no tenant is set, so RLS-protected rows are invisible (fail closed)."""
    with transaction(request.app.state.engine) as session:
        yield session
