from datetime import date
from decimal import Decimal

import pytest

from rebate_matching.normalise import normalise_amount, normalise_date, normalise_ref, normalise_sku


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("inv-10099", "INV10099"),
        ("INV 10003", "INV10003"),
        ("  INV-10003 ", "INV10003"),
        ("CL-2026-000001", "CL2026000001"),
        ("0012", "12"),
        ("000", "0"),
        ("INV-0012", "INV0012"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalise_ref(raw: str | None, expected: str) -> None:
    assert normalise_ref(raw) == expected


def test_sku_alias_resolution() -> None:
    assert normalise_sku("brk 3p/100a", {"BRK3P100A": "BRK3P100"}) == "BRK3P100"
    assert normalise_sku("PNL-200A") == "PNL200A"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2,050.00", "2050.00"), ("$5", "5"), ("(50.00)", "-50.00"), (7, "7"), (Decimal("1.5"), "1.5")],
)
def test_normalise_amount(raw: object, expected: str) -> None:
    assert normalise_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("bad", [1.5, True, None, "abc", "NaN"])
def test_normalise_amount_rejects(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        normalise_amount(bad)


def test_normalise_date() -> None:
    assert normalise_date("2026-07-31") == date(2026, 7, 31)
    assert normalise_date("2026-07-31T10:00:00Z") == date(2026, 7, 31)
