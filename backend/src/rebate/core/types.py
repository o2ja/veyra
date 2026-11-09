"""Column types shared by module models."""

from typing import Any

from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class CIText(UserDefinedType[str]):
    """PostgreSQL ``citext`` (case-insensitive text; extension created by the baseline)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "CITEXT"


ischema_names["citext"] = CIText  # so reflection (alembic check) recognises the type
