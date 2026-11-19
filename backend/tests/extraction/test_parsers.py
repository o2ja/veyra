"""Deterministic value parsers (S6.3). Expected values are hand-derived from the input strings."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rebate.modules.extraction import parsers as p


def test_normalise_folds_case_whitespace_typography_and_maps_back() -> None:
    src = (
        "Std"
        + chr(0xA0)
        + "Cost "
        + chr(0x201C)
        + "SPA"
        + chr(0x201D)
        + "\n\n  "
        + chr(0x2013)
        + " 64.50"
        + chr(0x200B)
        + "!"
    )
    out, index = p.normalise(src)
    assert out == 'std cost "spa" - 64.50!'
    assert len(out) == len(index)
    assert src[index[out.index("64.50")]] == "6"
    assert src[index[-1]] == "!"  # the zero-width space was dropped


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,000,000 - $1,999,999.99 2.5%", ["1000000", "1999999.99", "2.5"]),
        ("BRK-3P-100A 100.00 78.00 88.00 -", ["100.00", "78.00", "88.00"]),
        ("SPA-VOLT-2026-884 WIRE-01-CU 0.021", ["0.021"]),
        ("credit (12.50) and -3 and $ 7", ["-12.50", "-3", "7"]),
        ("tier 1 $0", ["1", "0"]),
        ("1,00 is malformed; 12,345 is not", ["12345"]),
        ("v1.2 and 3.4.5", []),
    ],
)
def test_numbers(text: str, expected: list[str]) -> None:
    assert p.numbers(text) == [Decimal(x) for x in expected]


def test_parse_decimal_rejects_floats_and_bools() -> None:
    assert p.parse_decimal(" 1,250.50 ") == Decimal("1250.50")
    assert p.parse_decimal(7) == Decimal(7)
    for bad in (0.1, True):
        with pytest.raises(TypeError):
            p.parse_decimal(bad)
    with pytest.raises(ValueError):
        p.parse_decimal("12 dollars")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.5%", ["0.015"]),
        ("rate 2.5 percent", ["0.025"]),
        ("12 % and 3.5%", ["0.12", "0.035"]),
        ("1,234.5%", []),
    ],
)
def test_percents(text: str, expected: list[str]) -> None:
    assert p.percents(text) == [Decimal(x) for x in expected]


@given(st.decimals(min_value=0, max_value=1000, places=3, allow_nan=False, allow_infinity=False))
def test_percent_round_trip_is_exact(value: Decimal) -> None:
    assert p.percents(f"{value}%") == [value / 100]


def test_dates_in_documented_formats() -> None:
    found = p.dates(
        "Effective: January 1, 2026 through Sept. 30th, 2026; signed 5 March 2026; ISO 2026-12-31; 13/01/2026"
    )
    assert found == [
        (date(2026, 1, 1), False),
        (date(2026, 9, 30), False),
        (date(2026, 3, 5), False),
        (date(2026, 12, 31), False),
    ]  # 13/01/2026 is not a valid US date and is ignored


def test_numeric_dates_are_us_and_flag_ambiguity() -> None:
    assert p.dates("Valid 03/01/2026 - 08/31/2026") == [(date(2026, 3, 1), True), (date(2026, 8, 31), False)]
    assert p.dates("on 04/04/2026") == [(date(2026, 4, 4), False)]  # same either way


def test_invalid_dates_and_identifiers_are_not_dates() -> None:
    assert p.dates("February 30, 2026 and SPA-2026-01-01X and 2026-13-01") == []


def test_calendar_year_states_both_ends() -> None:
    assert p.dates("Program year: calendar year 2026") == [
        (date(2026, 1, 1), False),
        (date(2026, 12, 31), False),
    ]
    assert p.measurement_periods("calendar year 2026") == [
        {"kind": "calendar_year", "start": "2026-01-01", "end": "2026-12-31"}
    ]


def test_parse_iso_date() -> None:
    assert p.parse_iso_date("2026-02-28") == date(2026, 2, 28)
    for bad in ("2026-2-28", "02/28/2026", "2026-02-30", 20260228, None):
        with pytest.raises(ValueError):
            p.parse_iso_date(bad)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-01", date(2026, 1, 1)),
        ("January 1, 2026", date(2026, 1, 1)),
        ("Dec. 31st, 2026", date(2026, 12, 31)),
        ("5 March 2026", date(2026, 3, 5)),
        ("12/31/2026", date(2026, 12, 31)),  # US numeric, unambiguous: the day cannot be 12
        ("04/04/2026", date(2026, 4, 4)),  # same either way
    ],
)
def test_parse_date_normalises_written_dates_to_iso(value: str, expected: date) -> None:
    assert p.parse_date(value) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "03/01/2026",  # March 1st or January 3rd: never guessed
        "January 1, 2026 through December 31, 2026",  # two dates, not one
        "calendar year 2026",  # two dates
        "2026-02-30",
        "February 30, 2026",
        "sometime in spring",
        20260101,
        None,
    ],
)
def test_parse_date_refuses_to_guess(bad: object) -> None:
    with pytest.raises(ValueError):
        p.parse_date(bad)


def test_name_key_ignores_case_punctuation_and_the_dataset_label() -> None:
    assert p.name_key("VOLTARA ELECTRIC (SYNTHETIC)") == p.name_key("Voltara Electric") == "voltara electric"
    assert p.name_key("Alder  Electric, LLC.") == "alder electric llc"
    assert p.name_key("Alder Electric") != p.name_key("Alder Electric LLC")  # different words stay different


def test_uoms_and_parse_uom() -> None:
    assert p.uoms("Allowance is per foot shipped") == {"FT"}
    assert p.uoms("each unit; reels of 500 ft") == {"EA", "RL", "FT"}
    assert p.uoms("Breaker 3-pole") == set()
    assert p.parse_uom(" ea ") == "EA"
    for bad in ("", "E A", 3, "per-foot"):
        with pytest.raises(ValueError):
            p.parse_uom(bad)


def test_identifiers_match_whole_tokens_only() -> None:
    text = "Agreement No. SPA-VOLT-2026-884. Items: BRK-3P-60A, PNL-200A"
    assert p.contains_identifier(text, "SPA-VOLT-2026-884")
    assert p.contains_identifier(text, "brk-3p-60a")
    assert not p.contains_identifier(text, "SPA-VOLT-2026-88")  # prefix of a longer number
    assert not p.contains_identifier(text, "VOLT-2026-884")  # suffix
    assert not p.contains_identifier(text, "BRK-3P-6A")
    assert p.parse_identifier(" brk-3p-60a ") == "BRK-3P-60A"
    for bad in ("BRK 3P", "", "-X", "x" * 65, 5):
        with pytest.raises(ValueError):
            p.parse_identifier(bad)


def test_phrases_match_on_word_boundaries_ignoring_case_and_line_breaks() -> None:
    text = "Eligible: Delta Power\nServices, Evergreen Building Systems."
    assert p.contains_phrase(text, "delta power services")
    assert not p.contains_phrase(text, "Delta Power Serv")
    assert not p.contains_phrase(text, "  ")


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("date_basis", "Eligibility is determined by the ship date of the product.", {"ship_date"}),
        ("date_basis", "ship-date governs", {"ship_date"}),
        ("date_basis", "invoice date governs", {"invoice_date"}),
        ("date_basis", "ship date, but claims within 60 days of invoice date", {"ship_date", "invoice_date"}),
        ("calculation_basis", "the difference between Standard Cost and SPA Cost", {"price_difference"}),
        ("calculation_basis", "Credit equals Standard Cost minus SPA Cost per unit.", {"price_difference"}),
        ("calculation_basis", "Allowance is per foot shipped", {"flat_per_unit"}),
        ("calculation_basis", "Guaranteed margin 12%", {"guaranteed_margin"}),
        ("calculation_basis", "2% of the sell price", {"percent_of_sell"}),
        ("tier_mode", "applies retroactively to the first dollar", {"retroactive"}),
        ("volume_basis", "qualifying sales of Northwind products", {"sales"}),
        ("volume_basis", "Net Purchases of all Voltara products", {"purchases"}),
        (
            "spa_lines_policy",
            "SPA product counts toward the qualifying sales used to determine the tier, but does not itself earn rebate.",
            {"qualify_only"},
        ),
        ("spa_lines_policy", "SPA sales do not count toward the tier", {"exclude"}),
        ("customer_scope", "applies only to sales by Distributor to: Alder", {"include"}),
        ("customer_scope", "Foxhall is not eligible", {"exclude"}),
        ("spa_reference_required", "Claims must reference the SPA number", {True}),
        ("agreement_type", "VOLTARA ELECTRIC - SPECIAL PRICING AGREEMENT", {"spa"}),
        ("agreement_type", "ANNUAL VOLUME INCENTIVE PROGRAM", {"rebate"}),
        ("cost_source", "replacement cost at time of sale", {"replacement_cost"}),
        ("tier_mode", "Rebates are paid annually.", set()),
    ],
)
def test_keyword_values(kind: str, text: str, expected: set[object]) -> None:
    assert p.keyword_values(kind, text) == expected


def test_rounding_parts() -> None:
    assert p.rounding_parts("Credits are calculated per line and rounded to the nearest cent.") == {
        "mode": {"half_up"},
        "scale": {2},
        "timing": {"per_line"},
    }
    assert p.rounding_parts("Rebates are rounded to the nearest cent.") == {
        "mode": {"half_up"},
        "scale": {2},
        "timing": set(),
    }
    assert p.rounding_parts("amounts truncated to 3 decimal places per invoice") == {
        "mode": {"down"},
        "scale": {3},
        "timing": {"per_document"},
    }
