"""Verifier rules (S6.3): candidate format, evidence location/quote/value checks, pass agreement, confidence.

Documents are built in the test (SYNTHETIC); expected outcomes follow AI_PROCESSING.md §4-§6 by hand.
"""

import dataclasses
from typing import Any

import pytest

from rebate.modules.extraction import (
    CandidateFormatError,
    Cell,
    Confidence,
    PdfText,
    SheetText,
    extract_pdf,
    flatten,
    run_verification,
)
from rebate.modules.extraction.verification import (
    CandidateField,
    Evidence,
    Status,
    find_quote,
    verify_field,
)
from tests.extraction.pdfgen import Run, build_pdf

LINES = [
    "SYNTHETIC SPECIAL PRICING AGREEMENT SPA-TEST-2026-001",
    "Effective March 1, 2026 through June 30, 2026. Ship date governs.",
    "Eligible: Alder Electric LLC. Not eligible: Foxhall Industrial.",
    "Credit equals Standard Cost minus SPA Cost. Rounded to the nearest cent per line.",
    "Part number Standard cost SPA cost UoM",
    "BRK-3P-60A 64.50 50.00 EA",
    "PNL-200A 412.00 360.00 EA",
    "Rebate rate 2.5% for calendar year 2026.",
    "SPA transactions are not eligible for annual volume rebate.",
]


@pytest.fixture(scope="module")
def doc() -> PdfText:
    result = extract_pdf(build_pdf([[Run(t, y=740 - 20 * i) for i, t in enumerate(LINES)]]))
    assert isinstance(result, PdfText)
    assert result.pages[0].text == "\n".join(LINES)
    return result


def w(value: Any, quote: str, page: int = 1) -> dict[str, Any]:
    return {"value": value, "evidence": {"page": page, "quote": quote}}


def field(path: str, value: Any, quote: str | None, **ev: Any) -> CandidateField:
    evidence = None if quote is None else Evidence(quote=quote, **({"page": 1} | ev))
    return CandidateField(path, value, evidence)


def check(
    doc: PdfText | SheetText, path: str, value: Any, quote: str | None, **ev: Any
) -> tuple[Status, tuple[str, ...]]:
    r = verify_field(doc, field(path, value, quote, **ev))
    return r.status, r.reasons


# ---- value rules per kind ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "quote", "ok"),
    [
        ("items[0].deviated_cost", "50.00", "BRK-3P-60A 64.50 50.00 EA", True),
        ("items[0].deviated_cost", "50", "BRK-3P-60A 64.50 50.00 EA", True),  # numeric equality
        ("items[0].deviated_cost", "60", "BRK-3P-60A 64.50 50.00 EA", False),  # 60 is part of the SKU
        ("items[0].max_quantity", "3", "BRK-3P-60A 64.50 50.00 EA", False),
        ("items[0].rate", "0.025", "Rebate rate 2.5% for calendar year 2026.", True),
        ("items[0].rate", "2.5", "Rebate rate 2.5% for calendar year 2026.", True),  # literal number present
        ("items[0].rate", "0.25", "Rebate rate 2.5% for calendar year 2026.", False),
        ("header.valid_from", "2026-03-01", "Effective March 1, 2026 through June 30, 2026.", True),
        ("header.valid_to", "2026-06-30", "Effective March 1, 2026 through June 30, 2026.", True),
        ("header.valid_to", "2026-07-30", "Effective March 1, 2026 through June 30, 2026.", False),
        ("header.external_number", "SPA-TEST-2026-001", "SPA-TEST-2026-001", True),
        ("header.external_number", "SPA-TEST-2026-00", "SPA-TEST-2026-001", False),
        ("items[0].part_number", "PNL-200A", "PNL-200A 412.00 360.00 EA", True),
        ("items[0].uom", "EA", "PNL-200A 412.00 360.00 EA", True),
        ("items[0].uom", "FT", "PNL-200A 412.00 360.00 EA", False),
        ("customers[0].name", "Alder Electric LLC", "Eligible: Alder Electric LLC.", True),
        ("customers[0].name", "Alder Electric", "Eligible: Alder Electric LLC.", True),  # phrase present
        ("customers[0].name", "Alder Electrical", "Eligible: Alder Electric LLC.", False),
        ("header.date_basis", "ship_date", "Ship date governs.", True),
        ("header.date_basis", "invoice_date", "Ship date governs.", False),
        ("rules.calculation_basis", "price_difference", "Credit equals Standard Cost minus SPA Cost.", True),
        ("rules.calculation_basis", "flat_per_unit", "Credit equals Standard Cost minus SPA Cost.", False),
        (
            "rules.rounding",
            {"mode": "half_up", "scale": 2, "timing": "per_line"},
            "Rounded to the nearest cent per line.",
            True,
        ),
        (
            "rules.rounding",
            {"mode": "half_up", "scale": 0, "timing": "per_line"},
            "Rounded to the nearest cent per line.",
            False,
        ),
        (
            "rules.measurement_period",
            {"kind": "calendar_year", "start": "2026-01-01", "end": "2026-12-31"},
            "calendar year 2026",
            True,
        ),
        (
            "rules.measurement_period",
            {"kind": "calendar_year", "start": "2026-01-01", "end": "2027-12-31"},
            "calendar year 2026",
            False,
        ),
        (
            "exclusions_text[0]",
            "SPA transactions are not eligible for annual volume rebate",
            "SPA transactions are not eligible for annual volume rebate.",
            True,
        ),
    ],
)
def test_value_must_be_derivable_from_the_matched_text(
    doc: PdfText, path: str, value: Any, quote: str, ok: bool
) -> None:
    status, reasons = check(doc, path, value, quote)
    assert (status is Status.verified) is ok, reasons


def test_value_is_read_from_the_document_not_from_the_models_quote(doc: PdfText) -> None:
    # A long quote with one changed digit is rejected (digits must match exactly), so a model can never
    # smuggle a number in through fuzzy matching.
    status, reasons = check(doc, "items[0].deviated_cost", "51.00", "BRK-3P-60A 64.50 51.00 EA and more text")
    assert status is Status.unsupported and reasons == ("quote_not_found",)


def test_customer_scope_comes_from_the_naming_sentence(doc: PdfText) -> None:
    assert check(doc, "customers[0].scope", "include", "Eligible: Alder Electric LLC.")[0] is Status.verified
    assert (
        check(doc, "customers[0].scope", "exclude", "Not eligible: Foxhall Industrial.")[0] is Status.verified
    )
    status, reasons = check(doc, "customers[0].scope", "include", "Not eligible: Foxhall Industrial.")
    assert status is Status.unsupported and reasons == ("value_contradicts_evidence",)


def test_ambiguous_and_underivable_interpretations_fail_closed(doc: PdfText) -> None:
    status, reasons = check(doc, "rules.tier_mode", "retroactive", "Rebate rate 2.5% for calendar year 2026.")
    assert status is Status.unsupported and reasons == ("value_not_derivable",)
    status, reasons = check(
        doc,
        "rules.rounding",
        {"mode": "half_up", "scale": 2, "timing": "per_document"},
        "Rounded to the nearest cent per line.",
    )
    assert reasons == ("value_contradicts_evidence",)
    status, reasons = check(
        doc,
        "rules.rounding",
        {"mode": "half_up", "scale": 2, "timing": "per_line"},
        "Rounded to the nearest cent",
    )
    assert reasons == ("value_not_derivable",)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("header.date_basis", "ship"),
        ("rules.spa_reference_required", "yes"),
        ("header.agreement_type", True),
        ("items[0].deviated_cost", "fifty"),
        ("header.valid_from", "03/01/2026"),
        ("rules.rounding", {"mode": "half_up", "scale": True, "timing": "per_line"}),
        ("rules.rounding", {"mode": "ceiling", "scale": 2, "timing": "per_line"}),
        ("customers[0].name", "   "),
    ],
)
def test_malformed_values_are_unsupported(doc: PdfText, path: str, value: Any) -> None:
    status, reasons = check(doc, path, value, "Ship date governs.")
    assert status is Status.unsupported and reasons[0] == "value_invalid"


def test_evidence_rules(doc: PdfText) -> None:
    assert check(doc, "header.valid_from", "2026-03-01", None) == (Status.unsupported, ("missing_evidence",))
    assert check(doc, "header.valid_from", "2026-03-01", "   ") == (Status.unsupported, ("missing_evidence",))
    assert check(doc, "header.valid_from", "2026-03-01", "Effective March 1, 2026", page=2)[1] == (
        "location_not_found",
    )
    assert check(doc, "header.valid_from", "2026-03-01", "Effective March 1, 2026", page=None)[1] == (
        "location_invalid",
    )
    assert check(doc, "header.valid_from", "2026-03-01", "Effective March 1, 2026", cell="A1")[1] == (
        "location_invalid",
    )
    assert check(doc, "header.valid_from", None, "anything") == (Status.absent, ())


def test_find_quote_normalises_and_maps_back_to_source_offsets() -> None:
    text = "Line one.\nCredit equals  Standard\nCost minus SPA Cost."
    s, e = find_quote("credit equals standard cost", text)  # type: ignore[misc]
    assert text[s:e] == "Credit equals  Standard\nCost"
    assert find_quote("", text) is None
    assert find_quote("credit equalz", text) is None  # short quotes must match exactly
    s, e = find_quote("Credit equals Standard Cost minus SPA Kost.", text)  # type: ignore[misc]
    assert text[s:e] == "Credit equals  Standard\nCost minus SPA Cost."  # fuzzy >= 0.95, same digits
    assert find_quote("Credit equals Standard Cost minus 2 SPA Cost.", text) is None  # a digit added


def test_hidden_and_tiny_text_cannot_be_evidence() -> None:
    doc = extract_pdf(
        build_pdf(
            [
                [
                    Run("Visible: SPA cost 50.00", y=700),
                    Run("SPA cost 1.00", y=650, rgb=(1, 1, 1)),
                    Run("Fine print SPA cost 2.00", y=600, size=3),
                ]
            ]
        )
    )
    assert isinstance(doc, PdfText)
    assert check(doc, "items[0].deviated_cost", "50.00", "SPA cost 50.00")[0] is Status.verified
    assert check(doc, "items[0].deviated_cost", "1.00", "SPA cost 1.00")[1] == ("evidence_in_hidden_text",)
    assert check(doc, "items[0].deviated_cost", "2.00", "Fine print SPA cost 2.00")[1] == (
        "evidence_in_hidden_text",
    )


def test_excel_evidence_by_cell() -> None:
    doc = SheetText((Cell("Items", "A2", "Guaranteed margin 12%"), Cell("Items", "B5", "LED-2X4-40W")))
    assert (
        check(doc, "items[0].rate", "0.12", "margin 12%", page=None, cell="A2")[0] is Status.verified
    )  # one sheet
    assert check(doc, "items[0].rate", "0.12", "margin 12%", page=None, cell="Items!a2")[0] is Status.verified
    assert check(doc, "items[0].rate", "0.12", "margin 12%", page=None, cell="B5")[1] == ("quote_not_found",)
    assert check(doc, "items[0].rate", "0.12", "margin 12%", page=None, cell="Z9")[1] == (
        "location_not_found",
    )
    assert check(doc, "items[0].rate", "0.12", "margin 12%")[1] == ("location_invalid",)  # a page in a sheet
    two = SheetText((*doc.cells, Cell("Other", "A1", "x")))
    assert check(two, "items[0].rate", "0.12", "margin 12%", page=None, cell="A2")[1] == ("location_invalid",)


# ---- candidate format ----------------------------------------------------------------------------------


def test_flatten_paths_and_customer_scope_shares_the_name_evidence() -> None:
    flat = flatten(
        {
            "schema_version": 1,
            "header": {"valid_from": w("2026-03-01", "Effective March 1, 2026")},
            "customers": [{"name": w("Alder Electric LLC", "Eligible: Alder"), "scope": "include"}],
            "items": [{"part_number": w("PNL-200A", "PNL-200A"), "rate": {"value": None, "evidence": None}}],
            "exclusions_text": [{"text": "SPA excluded", "evidence": {"page": 1, "quote": "SPA excluded"}}],
            "extractor_notes": ["never executed"],
        }
    )
    assert set(flat) == {
        "header.valid_from",
        "customers[0].name",
        "customers[0].scope",
        "items[0].part_number",
        "items[0].rate",
        "exclusions_text[0]",
    }
    assert flat["customers[0].scope"].evidence == flat["customers[0].name"].evidence
    assert flat["items[0].rate"].evidence is None


@pytest.mark.parametrize(
    "bad",
    [
        {"schema_version": 2},
        {"schema_version": 1, "surprise": {}},
        {"schema_version": 1, "header": {"invented_field": w("x", "x")}},
        {"schema_version": 1, "header": {"valid_from": "2026-03-01"}},  # not wrapped
        {"schema_version": 1, "items": [{"deviated_cost": {"value": 50.0, "evidence": None}}]},  # float
        {
            "schema_version": 1,
            "items": [{"deviated_cost": {"value": "50", "evidence": {"page": "1", "quote": "x"}}}],
        },
        {
            "schema_version": 1,
            "items": [{"deviated_cost": {"value": "50", "evidence": {"page": 1, "quote": 5}}}],
        },
        {
            "schema_version": 1,
            "items": [{"deviated_cost": {"value": "50", "evidence": {"page": 1, "quote": "x", "url": "y"}}}],
        },
        {"schema_version": 1, "items": {"0": {}}},
        {"schema_version": 1, "customers": [{"name": w("A", "A"), "scope": "include", "code": "X"}]},
        {
            "schema_version": 1,
            "header": {"valid_from": {"value": "2026-03-01", "evidence": None, "confidence": 0.99}},
        },
        [],
    ],
)
def test_flatten_rejects_malformed_output(bad: Any) -> None:
    with pytest.raises(CandidateFormatError):
        flatten(bad)


# ---- pass agreement and confidence --------------------------------------------------------------------


def _pass(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "header": {
            "agreement_type": w("spa", "SPECIAL PRICING AGREEMENT"),
            "external_number": w("SPA-TEST-2026-001", "SPA-TEST-2026-001"),
            "valid_from": w("2026-03-01", "Effective March 1, 2026"),
            "date_basis": w("ship_date", "Ship date governs."),
        },
        "rules": {"tier_mode": {"value": None, "evidence": None}},
        "items": [
            {
                "part_number": w("BRK-3P-60A", "BRK-3P-60A 64.50 50.00 EA"),
                "deviated_cost": w("50.00", "BRK-3P-60A 64.50 50.00 EA"),
            },
            {
                "part_number": w("PNL-200A", "PNL-200A 412.00 360.00 EA"),
                "deviated_cost": w("360.00", "PNL-200A 412.00 360.00 EA"),
            },
        ],
    }
    for key, value in over.items():
        section, name = key.split("__")
        if value is None:
            base[section].pop(name)
        else:
            base[section][name] = value
    return base


def test_agreeing_passes_are_high_and_rows_align_by_content(doc: PdfText) -> None:
    b = _pass()
    b["items"].reverse()
    report = run_verification(doc, _pass(), b)
    assert report.fields["items[PNL-200A].deviated_cost"].confidence is Confidence.high
    assert report.fields["items[PNL-200A].deviated_cost"].value == "360.00"
    assert report.draft_confidence is Confidence.high
    assert report.blocking == () and not report.needs_manual_entry
    # tier_mode is a rebate rule both passes left null in an SPA: not applicable, dropped
    assert "rules.tier_mode" not in report.fields


def test_critical_disagreement_is_a_conflict_with_both_values(doc: PdfText) -> None:
    b = _pass()
    b["items"][0]["deviated_cost"] = w("64.50", "BRK-3P-60A 64.50 50.00 EA")  # verifiable, but another column
    report = run_verification(doc, _pass(), b)
    f = report.fields["items[BRK-3P-60A].deviated_cost"]
    assert f.confidence is Confidence.conflict and f.value is None
    assert (f.a.value, f.b.value) == ("50.00", "64.50")  # type: ignore[union-attr]
    assert report.blocking == ("items[BRK-3P-60A].deviated_cost",)
    assert report.draft_confidence is Confidence.conflict


def test_non_critical_disagreement(doc: PdfText) -> None:
    # one pass null, the other verified: keep the verified value, flag low
    report = run_verification(doc, _pass(), _pass(header__external_number={"value": None, "evidence": None}))
    f = report.fields["header.external_number"]
    assert (f.confidence, f.value, f.critical) == (Confidence.low, "SPA-TEST-2026-001", False)
    assert "passes_disagree" in f.reasons and report.blocking == ()
    # two different verified values: conflict even for a non-critical field
    other = _pass(header__agreement_type=w("rebate", "Rebate rate 2.5%"))
    assert (
        run_verification(doc, _pass(), other).fields["header.agreement_type"].confidence
        is Confidence.conflict
    )


def test_ocr_text_lowers_confidence(doc: PdfText) -> None:
    ocr = PdfText((dataclasses.replace(doc.pages[0], text_method="ocr"),))
    f = run_verification(ocr, _pass(), _pass()).fields["header.valid_from"]
    assert f.confidence is Confidence.medium and "ocr_text" in f.reasons


def test_validator_warnings_lower_confidence(doc: PdfText) -> None:
    report = run_verification(doc, _pass(), _pass(), warned_keys=frozenset({"header.valid_from"}))
    assert report.fields["header.valid_from"].confidence is Confidence.medium
    assert report.draft_confidence is Confidence.medium
    assert "header.valid_from" in report.needs_acknowledgement


def test_draft_needs_manual_entry_when_verification_wipes_all_critical_fields(doc: PdfText) -> None:
    def fabricated() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "header": {
                "valid_from": w("2026-01-01", "Effective January 1, 2026"),
                "external_number": w("SPA-TEST-2026-001", "SPA-TEST-2026-001"),
            },
            "items": [{"deviated_cost": w("9.99", "SPA cost 9.99")}],
        }

    report = run_verification(doc, fabricated(), fabricated())
    assert report.needs_manual_entry
    assert report.draft_confidence is Confidence.unsupported
    assert report.fields["header.external_number"].value == "SPA-TEST-2026-001"  # non-critical survives


def test_schema_validator_hook_rejects_a_pass(doc: PdfText) -> None:
    seen: list[Any] = []

    def validator(raw: Any) -> list[str]:
        seen.append(raw)
        return ["header.valid_to: required"] if "valid_to" not in raw["header"] else []

    with pytest.raises(CandidateFormatError, match="valid_to: required"):
        run_verification(doc, _pass(), _pass(), schema_validator=validator)
    assert len(seen) == 1
    ok = _pass(header__valid_to=w("2026-06-30", "through June 30, 2026"))
    assert (
        run_verification(doc, ok, ok, schema_validator=validator).fields["header.valid_to"].confidence
        is Confidence.high
    )


def test_model_self_reported_confidence_is_not_accepted(doc: PdfText) -> None:
    raw = _pass()
    raw["header"]["valid_from"]["confidence"] = "high"
    with pytest.raises(CandidateFormatError):
        run_verification(doc, raw, _pass())
