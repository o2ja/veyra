from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from rebate.core.money import Money
from rebate_engine.money import MONEY_CONTEXT, to_decimal


@pytest.mark.parametrize("value", [1.5, 0.1, float("nan"), True, False, None, [1], b"1"])
def test_rejects_non_exact_types(value: object) -> None:
    with pytest.raises(TypeError):
        to_decimal(value)


@pytest.mark.parametrize("value", ["abc", "", "1,000.00", "NaN", "Infinity", "-inf", Decimal("NaN")])
def test_rejects_non_finite_or_malformed(value: object) -> None:
    with pytest.raises(ValueError):
        to_decimal(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2200.00", "2200.00"), (" 0.025 ", "0.025"), (7, "7"), (Decimal("-1.10"), "-1.10")],
)
def test_accepts_exact_inputs_unchanged(value: object, expected: str) -> None:
    result = to_decimal(value)
    assert isinstance(result, Decimal)
    assert str(result) == expected  # scale preserved; nothing rounded


@given(st.decimals(allow_nan=False, allow_infinity=False))
def test_string_round_trip_is_exact(d: Decimal) -> None:
    assert to_decimal(str(d)) == d


def test_context_is_spec_value() -> None:
    assert MONEY_CONTEXT.prec == 28
    assert MONEY_CONTEXT.rounding == "ROUND_HALF_UP"


class _Payload(BaseModel):
    amount: Money


def test_pydantic_boundary_rejects_json_float_and_serialises_string() -> None:
    with pytest.raises(ValidationError):
        _Payload.model_validate_json('{"amount": 1.5}')
    with pytest.raises(ValidationError):
        _Payload.model_validate_json('{"amount": "abc"}')
    p = _Payload.model_validate_json('{"amount": "2200.00"}')
    assert p.amount == Decimal("2200.00")
    assert p.model_dump_json() == '{"amount":"2200.00"}'
