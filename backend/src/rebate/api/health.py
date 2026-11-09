"""Unauthenticated health endpoints (API_SPECIFICATION.md: public list). No sensitive data."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from rebate.core.errors import Unavailable

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"]


@router.get("/live", operation_id="health_live")
def live() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/ready", operation_id="health_ready", responses={503: {"description": "Database unavailable"}})
def ready(request: Request) -> HealthStatus:
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise Unavailable("Database is unavailable.") from None
    return HealthStatus(status="ok")
