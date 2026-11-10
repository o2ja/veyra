"""Money primitives shared by the engine and the application (ADR-015, BUSINESS_LOGIC.md §1).

Pure: no imports from ``rebate``. ``quantize`` applies an agreement rounding mode (§3.1, §6);
per-document allocation lives in ``rounding.py``.
"""

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, InvalidOperation

MONEY_CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)

# Agreement `rounding.mode` values -> decimal rounding constants.
ROUNDING_MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "down": ROUND_DOWN}


def to_decimal(value: object) -> Decimal:
    """Convert an exact input to ``Decimal``. Floats (and bools) are rejected, never coerced."""
    if isinstance(value, bool | float):
        raise TypeError(f"{type(value).__name__} is not allowed for money; use a decimal string")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"not a decimal number: {value!r}") from None
    else:
        raise TypeError(f"{type(value).__name__} is not allowed for money")
    if not result.is_finite():
        raise ValueError("money must be finite")
    return result


def quantize(value: Decimal, scale: int, mode: str) -> Decimal:
    """Round ``value`` to ``scale`` decimal places with an agreement rounding mode."""
    if mode not in ROUNDING_MODES:
        raise ValueError(f"unknown rounding mode: {mode!r}")
    if not 0 <= scale <= 6:
        raise ValueError(f"rounding scale must be 0..6, got {scale}")
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUNDING_MODES[mode], context=MONEY_CONTEXT)
