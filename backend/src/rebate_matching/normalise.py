"""Normalisation of references, SKUs, amounts and dates (BUSINESS_LOGIC.md §10.2).

Rule: trim, uppercase, strip non-alphanumerics; an identifier that is then purely numeric also loses its
leading zeros ("0012" -> "12", "000" -> "0"). Mixed identifiers ("INV-0012" -> "INV0012") keep their zeros
(conservative: a missed T2 match falls through to a weaker tier, a wrong one would be a false positive).
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_NON_ALNUM = re.compile(r"[^0-9A-Z]")


def normalise_ref(value: str | None) -> str:
    """Normalise an invoice number, claim reference or SKU key; ``None``/blank -> ``""``."""
    if value is None:
        return ""
    out = _NON_ALNUM.sub("", value.strip().upper())
    if out.isdigit():
        out = out.lstrip("0") or "0"
    return out


def normalise_sku(value: str | None, aliases: dict[str, str] | None = None) -> str:
    """Normalise a SKU and resolve it through ``aliases`` (normalised alias -> normalised canonical SKU)."""
    key = normalise_ref(value)
    return (aliases or {}).get(key, key)


def normalise_amount(value: object) -> Decimal:
    """Exact money input -> ``Decimal``. Floats/bools are rejected. Accepts ``"1,234.50"``, ``"$5"``, ``"(50.00)"``."""
    if isinstance(value, bool | float):
        raise TypeError(f"{type(value).__name__} is not allowed for money; use a decimal string")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        try:
            result = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"not a decimal number: {value!r}") from None
    else:
        raise TypeError(f"{type(value).__name__} is not allowed for money")
    if not result.is_finite():
        raise ValueError("money must be finite")
    return result


def normalise_date(value: object) -> date:
    """``date`` or ISO-8601 string (``YYYY-MM-DD``, a time part is dropped) -> ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip()[:10])
    raise TypeError(f"{type(value).__name__} is not a date")
