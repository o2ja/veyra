"""Shared request validation types (API_SPECIFICATION.md §1.3)."""

import re
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field, StringConstraints

# Strict request models: unknown fields rejected, no type coercion.
STRICT = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

# ASCII only in Release 1 (S1 review finding 19): one canonical form for storage, lockout keys and
# lookups. Case is folded for keys; the DB column is citext.
_EMAIL = re.compile(r"^[!-~]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})+$")


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def normalize_email(value: str) -> str:
    if len(value) > 254 or not _EMAIL.fullmatch(value) or value.count("@") != 1:
        raise ValueError("Enter a valid email address.")
    local, _, domain = value.partition("@")
    return f"{local}@{domain.lower()}"


Email = Annotated[str, BeforeValidator(_strip), AfterValidator(normalize_email)]
Name = Annotated[str, BeforeValidator(_strip), StringConstraints(min_length=1, max_length=200)]
Token = Annotated[str, StringConstraints(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
TotpCode = Annotated[
    str,
    StringConstraints(pattern=r"^(\d{6}|[A-Za-z2-7]{4}-?[A-Za-z2-7]{4}-?[A-Za-z2-7]{4}-?[A-Za-z2-7]{4})$"),
]


def email_key(email: str) -> str:
    """Case-folded form used for HMAC keys (lockout/rate limits of unknown emails)."""
    return email.lower()


# JSON carries ids as strings; strict mode would refuse str -> UUID when FastAPI validates the parsed
# body, so id fields opt out of strictness (they are still parsed and validated as UUIDs).
JsonUUID = Annotated[UUID, Field(strict=False)]
