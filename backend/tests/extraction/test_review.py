"""Contract Review support: where each extracted field lands in the draft, and the found / needs-decision counts.
Expected values are derived by hand from the row order rules in ``payload.build_payload``."""

from typing import Any

from rebate.modules.extraction import payload, review
from rebate.modules.extraction.verification import Confidence, MergedField


def mf(key: str, value: Any, confidence: Confidence = Confidence.high) -> MergedField:
    return MergedField(key, True, value, confidence, (), None, None)


def fields_of(*items: MergedField) -> dict[str, MergedField]:
    return {f.key: f for f in items}


def test_payload_paths_follow_the_row_order_of_the_built_payload() -> None:
    fields = fields_of(
        mf("header.manufacturer_name", "Siemens"),
        mf("header.date_basis", "ship_date"),
        mf("rules.calculation_basis", "price_difference"),
        mf("items[A].part_number", "A"),
        mf("items[A].deviated_cost", "5.00"),
        mf("items[B].part_number", "B"),
        mf("items[C].deviated_cost", None, Confidence.conflict),  # all-empty row: dropped from the draft
        mf("customers[Acme].name", "Acme"),
        mf("tiers[1000].lower_bound", "1000"),
        mf("tiers[0].lower_bound", "0"),
        mf("exclusions_text[]", "No returns"),
    )
    assert payload.payload_paths(fields) == {
        "header.manufacturer_name": "header.manufacturer_code",
        "header.date_basis": "rules.date_basis",
        "rules.calculation_basis": "rules.calculation.basis",
        "items[A].part_number": "items[0].sku",
        "items[A].deviated_cost": "items[0].deviated_cost",
        "items[B].part_number": "items[1].sku",
        "customers[Acme].name": "customers[0].customer_code",
        "tiers[0].lower_bound": "tiers[0].lower_bound",
        "tiers[1000].lower_bound": "tiers[1].lower_bound",
    }


def test_decision_fields_the_document_does_not_state_are_not_defaulted() -> None:
    body = payload.build_payload(
        fields_of(mf("header.agreement_type", "spa"), mf("header.manufacturer_name", "Siemens")),
        manufacturer_codes={},
        customer_codes={},
    )
    rules = body["rules"]
    for missing in ("date_basis", "calculation", "rounding", "claim_uom"):
        assert missing not in rules


def result_of(*rows: tuple[str, str, Any, str | None, bool]) -> dict[str, Any]:
    return {
        "fields": [
            {"key": k, "payload_path": p, "value": v, "confidence": c, "evidence": {"page": 1} if e else None}
            for k, p, v, c, e in rows
        ]
    }


def test_counts_found_with_a_quote_and_needs_decision() -> None:
    result = result_of(
        ("a", "rules.date_basis", "ship_date", "high", True),  # found
        ("b", "rules.rounding", None, "low", False),  # not stated
        ("c", "header.manufacturer_code", "X", "high", True),  # found, but the validator rejects it
        ("d", "items[0].rate", "0.05", "medium", True),  # below high
    )
    validation = {
        "errors": [
            {"field": "payload.header.manufacturer_code", "code": "unresolved"},
            {"field": "payload.rules.rounding.mode", "code": "required"},  # explained by field b
            {"field": "payload.items", "code": "required"},  # no field explains it
        ]
    }
    # found: only a. needs: b, c, d, plus the one unexplained error
    assert review.counts(result, validation) == (1, 4)


def test_counts_without_a_result_is_none() -> None:
    assert review.counts(None, None) is None
