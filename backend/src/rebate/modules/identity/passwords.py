"""Password policy (SECURITY.md §1): length 12..256, not a common password (bundled list, S1 D-3),
not the email address. No composition rules."""

import gzip
import unicodedata
from functools import lru_cache
from importlib.resources import files

from rebate.core.config import Settings
from rebate.core.errors import PasswordTooCommon, ValidationFailed


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    data = files("rebate.modules.identity").joinpath("data/common-passwords-12plus.txt.gz").read_bytes()
    return frozenset(gzip.decompress(data).decode("utf-8").splitlines())


def _key(password: str) -> str:
    return unicodedata.normalize("NFKC", password).casefold()


def check(settings: Settings, password: str, email: str, field: str = "password") -> None:
    length = len(unicodedata.normalize("NFKC", password))
    if length < settings.password_min_length or length > settings.password_max_length:
        raise ValidationFailed(
            details=[
                {
                    "field": field,
                    "code": "length",
                    "message": (
                        f"Use {settings.password_min_length} to {settings.password_max_length} characters."
                    ),
                }
            ]
        )
    key = _key(password)
    if key in _common() or key == email.casefold():
        raise PasswordTooCommon(
            details=[{"field": field, "code": "too_common", "message": PasswordTooCommon.message}]
        )
