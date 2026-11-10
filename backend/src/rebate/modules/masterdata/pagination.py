"""Keyset pagination on UUIDv7 ids (API_SPECIFICATION.md §1.2): ``?limit=&cursor=``, response
``{"data": [...], "page": {"next_cursor", "has_more"}}``. The cursor is the opaque, url-safe base64 of
the last id; ids are time-ordered, so newest-first lists page stably while rows are being added."""

import base64
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select

from rebate.core.errors import ValidationFailed


class Page(BaseModel):
    next_cursor: str | None
    has_more: bool


def decode_cursor(cursor: str | None) -> UUID | None:
    if cursor is None:
        return None
    try:
        return UUID(base64.urlsafe_b64decode(cursor.encode() + b"==").decode())
    except ValueError, UnicodeDecodeError:
        raise ValidationFailed(
            details=[{"field": "cursor", "code": "invalid", "message": "Invalid cursor."}]
        ) from None


def encode_cursor(value: UUID) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def keyset(
    stmt: Select[Any], id_column: Any, cursor: str | None, limit: int, *, newest_first: bool = True
) -> Select[Any]:
    after = decode_cursor(cursor)
    if after is not None:
        stmt = stmt.where(id_column < after if newest_first else id_column > after)
    order = id_column.desc() if newest_first else id_column.asc()
    return stmt.order_by(order).limit(limit + 1)


def page[T](rows: Sequence[T], limit: int, id_of: Any = lambda r: r.id) -> tuple[list[T], Page]:
    more = len(rows) > limit
    data = list(rows[:limit])
    return data, Page(next_cursor=encode_cursor(id_of(data[-1])) if more and data else None, has_more=more)
