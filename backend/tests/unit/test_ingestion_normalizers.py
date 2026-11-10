"""Pure normalisers (S3.2): numbers, dates, codes, UoM, sign canonicalisation, PAN guard, header signature.
Expected values are hand-derived from the input text (TESTING_STRATEGY.md §11 item 11)."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rebate.modules.ingestion import normalizers as n

US = n.NumberFormat(".")
EU = n.NumberFormat(",")


def num(text: str, fmt: n.NumberFormat = US, scale: int = 6) -> Decimal:
    return n.parse_decimal(text, fmt, scale=scale, int_digits=12)


@pytest.mark.parametrize(
    ("text", "fmt", "expected"),
    [
        ("88.00", US, "88.00"),
        ("1,000", US, "1000"),
        ("88,000.00", US, "88000.00"),
        ("1 234 567.5", US, "1234567.5"),
        ("(1,234.50)", US, "-1234.50"),
        ("123.45-", US, "-123.45"),
        ("-0", US, "0"),
        ("$1,000.25", US, "1000.25"),
        ("+7", US, "7"),
        (".5", US, "0.5"),
        ("1.5E+3", US, "1500"),
        ("1.234,56", EU, "1234.56"),
        ("1 234,56", EU, "1234.56"),
        ("(12,5)", EU, "-12.5"),
        ("  42  ", US, "42"),
    ],
)
def test_numbers(text: str, fmt: n.NumberFormat, expected: str) -> None:
    assert num(text, fmt) == Decimal(expected)


@pytest.mark.parametrize(
    ("text", "fmt", "code"),
    [
        ("abc", US, "invalid_number"),
        ("1,23", US, "invalid_number"),  # misplaced grouping (ambiguous): never guessed
        ("1.234,56", US, "invalid_number"),
        ("1.2.3", US, "invalid_number"),
        ("12,34,567", US, "invalid_number"),
        ("NaN", US, "invalid_number"),
        ("Infinity", US, "invalid_number"),
        ("", US, "required"),
        ("1,234.56", EU, "invalid_number"),
    ],
)
def test_bad_numbers(text: str, fmt: n.NumberFormat, code: str) -> None:
    with pytest.raises(n.NormError) as err:
        num(text, fmt)
    assert err.value.code == code


def test_scale_and_range_are_errors_not_rounding() -> None:
    with pytest.raises(n.NormError, match="decimal places") as err:
        n.parse_decimal("0.12345", scale=4, int_digits=14)
    assert err.value.code == "too_many_decimals"
    assert n.parse_decimal("0.12340", scale=4, int_digits=14) == Decimal("0.1234")  # trailing zero is fine
    with pytest.raises(n.NormError) as big:
        n.parse_decimal("1000000000000", scale=6, int_digits=12)
    assert big.value.code == "out_of_range"


@given(
    st.decimals(min_value=Decimal("-999999999"), max_value=Decimal("999999999"), places=4, allow_nan=False)
)
def test_decimal_text_round_trips_losslessly(value: Decimal) -> None:
    """Money safety: canonical text parses back to the same value; no float on the way."""
    text = n.decimal_text(value, 4)
    assert n.parse_decimal(text, scale=4, int_digits=14) == value


@pytest.mark.parametrize(
    ("text", "order", "expected"),
    [
        ("2026-06-10", "MDY", date(2026, 6, 10)),
        ("2026/6/1", "MDY", date(2026, 6, 1)),
        ("2026-06-10 00:00:00", "MDY", date(2026, 6, 10)),
        ("2026-06-10T00:00:00", "MDY", date(2026, 6, 10)),
        ("06/10/2026", "MDY", date(2026, 6, 10)),
        ("6/12/2026", "MDY", date(2026, 6, 12)),
        ("6/12/2026", "DMY", date(2026, 12, 6)),
        ("12.06.2026", "DMY", date(2026, 6, 12)),
        ("46183", "MDY", date(2026, 6, 10)),  # Excel serial: 1899-12-30 + 46183 days
    ],
)
def test_dates(text: str, order: str, expected: date) -> None:
    assert n.parse_date(text, order) == expected


@pytest.mark.parametrize(
    "text", ["2026-13-45", "13/45/2026", "6/12/26", "2026-02-30", "yesterday", "123", "2026-06-10 13:00"]
)
def test_bad_dates(text: str) -> None:
    with pytest.raises(n.NormError) as err:
        n.parse_date(text)
    assert err.value.code == "invalid_date"


def test_codes_keep_leading_zeros_and_fold_case() -> None:
    assert n.canonical_code(" 00418 ") == "00418"
    assert n.canonical_code("brk-3p-100a") == "BRK-3P-100A"
    assert n.canonical_code("a  b") == "A B"
    with pytest.raises(n.NormError):
        n.canonical_code("   ")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("ea", "EA"), ("Each", "EA"), ("FEET", "FT"), ("reel", "RL"), ("CT", "CT"), ("box.", "BX")],
)
def test_uom(text: str, expected: str) -> None:
    assert n.canonical_uom(text) == expected


def test_uom_rejects_garbage() -> None:
    with pytest.raises(n.NormError) as err:
        n.canonical_uom("per foot!")
    assert err.value.code == "invalid_uom"


def test_credit_notes_are_negative_whatever_the_file_says() -> None:
    """BUSINESS_LOGIC.md §7: CN-5001 has qty 5 / amount 290.00 in the file -> -5 / -290.00."""
    assert n.canonical_sign(True, Decimal(5), Decimal("290.00")) == (Decimal(-5), Decimal("-290.00"))
    assert n.canonical_sign(True, Decimal(-5), Decimal("-290.00")) == (Decimal(-5), Decimal("-290.00"))
    assert n.canonical_sign(False, Decimal(10), Decimal("880.00")) == (Decimal(10), Decimal("880.00"))


@pytest.mark.parametrize(
    ("negative", "qty", "amount", "code"),
    [
        (False, "-10", "880", "invoice_negative_quantity"),
        (False, "0", "880", "zero_quantity"),
        (True, "0", "0", "zero_quantity"),  # price-only credit notes are not supported in Release 1
        (False, "10", "-880", "sign_mismatch"),
    ],
)
def test_sign_errors(negative: bool, qty: str, amount: str, code: str) -> None:
    with pytest.raises(n.NormError) as err:
        n.canonical_sign(negative, Decimal(qty), Decimal(amount))
    assert err.value.code == code


def test_doc_types() -> None:
    assert n.canonical_doc_type("CN", n.SALES_DOC_TYPES) == "credit_note"
    assert n.canonical_doc_type("inv", n.SALES_DOC_TYPES) == "invoice"
    assert n.canonical_doc_type("vendor_credit", n.PURCHASE_DOC_TYPES) == "vendor_credit"
    with pytest.raises(n.NormError):
        n.canonical_doc_type("quote", n.SALES_DOC_TYPES)


def test_header_signature_ignores_order_case_and_spacing() -> None:
    a = n.header_signature(["Inv #", "Qty  Shipped", "UM"])
    assert a == n.header_signature(["um", "inv #", "qty shipped"])
    assert a != n.header_signature(["Inv #", "Qty Shipped"])
    assert len(a) == 64


def test_luhn_and_pan_guard() -> None:
    assert n.luhn_ok("4111111111111111")
    assert not n.luhn_ok("4111111111111112")
    assert n.find_card_number("card 4111 1111 1111 1111 ok") == "4111 1111 1111 1111"
    assert n.find_card_number("5500-0000-0000-0004") == "5500-0000-0000-0004"
    assert n.find_card_number("INV-10001") is None
    assert n.find_card_number("4111111111111112") is None  # fails Luhn
    assert n.find_card_number("0000000000000000") is None  # not a card network prefix
    masked = n.mask_card_number("pay 4111111111111111 and 378282246310005")
    assert "4111" not in masked and "378282" not in masked and masked.count("[card number removed]") == 2


def test_text_value_bounds() -> None:
    assert n.text_value("  a   b ", max_length=10) == "a b"
    assert n.text_value("   ", max_length=10) is None
    with pytest.raises(n.NormError):
        n.text_value("x" * 11, max_length=10)


def test_currency_and_int() -> None:
    assert n.canonical_currency(" usd ") == "USD"
    with pytest.raises(n.NormError):
        n.canonical_currency("US")
    assert n.parse_int("3") == 3
    assert n.parse_int("3.0") == 3
    with pytest.raises(n.NormError):
        n.parse_int("3.5")
