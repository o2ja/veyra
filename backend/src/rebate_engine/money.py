"""Money primitives shared by the engine and the application (ADR-015, BUSINESS_LOGIC.md §1).

Pure: no imports from ``rebate``. Rounding by agreement mode/scale is engine logic (S5), not here.
"""

from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation

MONEY_CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)


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
