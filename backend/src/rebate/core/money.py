"""Money at the application boundary. The single implementation lives in ``rebate_engine.money``."""

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

from rebate_engine.money import MONEY_CONTEXT, to_decimal


def _validate(value: object) -> Decimal:
    try:
        return to_decimal(value)
    except TypeError as exc:  # pydantic only turns ValueError into a 422 validation error
        raise ValueError(str(exc)) from None


# Pydantic field type: accepts decimal strings/ints/Decimals, rejects JSON floats, serialises as string.
Money = Annotated[
    Decimal,
    BeforeValidator(_validate),
    PlainSerializer(str, return_type=str, when_used="json"),
]

__all__ = ["MONEY_CONTEXT", "Money", "to_decimal"]
