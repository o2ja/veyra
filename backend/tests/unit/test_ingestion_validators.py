"""Row validation (pure, S3.2/S3.3) against the SYNTHETIC files and expected_import_results.json."""

from typing import Any

import pytest

from rebate.modules.ingestion import readers
from rebate.modules.ingestion.validators import (
    FIELDS,
    Lookups,
    MappingInvalid,
    RowValidator,
    Validated,
    check_mapping,
    suggest_mapping,
)
from tests.testdata import data, truth

CUSTOMERS = {c: f"cust-{c}" for c in ("ALDER", "BLUFF", "CEDAR", "DELTA", "EVERG", "FOXHL", "GRANI")}
PRODUCTS = {
    s: (f"prod-{s}", "mfr-x")
    for s in (
        "BRK-3P-100A",
        "BRK-3P-60A",
        "PNL-200A",
        "PNL-400A",
        "00418",
        "LED-2X4-40W",
        "LED-HB-150W",
        "WIRE-12-2-RL",
    )
}


def lookups(**kw: Any) -> Lookups:
    return Lookups(
        customers=CUSTOMERS, products=PRODUCTS, branches={}, manufacturers={"VOLT": "mfr-volt"}, **kw
    )


def run(
    rel: str, import_type: str = "sales", lk: Lookups | None = None, options: dict[str, Any] | None = None
) -> tuple[list[Validated], dict[int, Any], RowValidator]:
    src = readers.csv_source(data(rel)) if rel.endswith(".csv") else None
    if src is None:
        with readers.xlsx_source(data(rel)) as x:
            recs = readers.records(x)
            rows = list(recs.rows)
    else:
        recs = readers.records(src)
        rows = list(recs.rows)
    mapping = suggest_mapping(import_type, recs.headers)
    opts = options or {}
    check_mapping(import_type, mapping, opts, recs.headers)
    v = RowValidator(import_type, mapping, opts, lk or lookups())
    results = [v.validate(n, raw) for n, raw in rows]
    return results, v.finish(), v


def codes(r: Validated) -> list[str]:
    return [i.code for i in r.issues if i.severity == "error"]


def test_error_file_matches_ground_truth() -> None:
    gt = truth("expected_import_results.json")["sales_with_errors_layoutA.csv"]
    results, extra, v = run("sales/sales_with_errors_layoutA.csv")
    assert len(results) == gt["rows_total"] == 8
    assert sum(r.status == "valid" for r in results) == gt["valid_inserted"] == 1
    assert sum(r.status == "duplicate" for r in results) == gt["duplicate_in_file"] == 1
    by_doc = {r.row_number: r for r in results}
    # rows 4..9 (file rows) are INV-30002..30007
    expected = {k: v_.split(" ")[0] for k, v_ in gt["rejected_rows"].items()}
    got = {f"INV-3000{r.row_number - 2}": codes(r)[0] for r in results if r.status == "error"}
    assert got == expected
    assert extra == {}
    assert by_doc[2].status == "valid" and by_doc[3].status == "duplicate"
    assert v.unmapped[("product", "NOPE-123")].count == 1  # unknown SKU goes to the review queue


def test_scenarios_layout_a_all_valid_and_credit_notes_negative() -> None:
    results, extra, _ = run("sales/sales_scenarios_layoutA.csv")
    assert len(results) == 23 and all(r.status == "valid" for r in results), [codes(r) for r in results]
    assert extra == {}  # CN-5001/5002 originals INV-10020/1, INV-10021/1 are in the file
    by_doc = {r.normalized["document_number"]: (r, r.normalized) for r in results if r.normalized}
    cn = {k: v for k, v in by_doc.items() if v[1]["doc_type"] == "credit_note"}
    assert set(cn) == {"CN-5001", "CN-5002"}
    assert cn["CN-5001"][1]["quantity"] == "-5.000000"  # file says 5 (BUSINESS_LOGIC.md §7)
    assert cn["CN-5001"][1]["extended_amount"] == "-290.0000"
    assert cn["CN-5002"][1]["quantity"] == "-4.000000" and cn["CN-5002"][0].phase == 2
    sku = by_doc["INV-10040"][1]
    assert sku["sku"] == "00418" and sku["product_id"] == "prod-00418"
    assert by_doc["INV-10036"][1]["unit_cost"] is None  # SC-16 blank cost stays blank (engine: missing_cost)


def test_layout_b_us_formats() -> None:
    gt = truth("expected_import_results.json")["sales_layoutB_us_formats.xlsx"]
    results, _, _ = run("sales/sales_layoutB_us_formats.xlsx")
    assert len(results) == gt["rows_total"] == 3
    assert all(r.status == "valid" for r in results), [codes(r) for r in results]
    n = [r.normalized for r in results if r.normalized]
    assert len(n) == 3
    assert (
        n[0]["quantity"] == "1000.000000" and n[0]["extended_amount"] == "88000.0000"
    )  # thousands separators
    assert n[2]["invoice_date"] == "2026-06-12"  # 6/12/2026 US date
    assert n[1]["sku"] == "00418"
    assert n[0]["doc_type"] == "invoice" and n[0]["currency_code"] == "USD"


def test_layout_b_with_dmy_reads_the_ambiguous_date_the_other_way() -> None:
    results, _, _ = run("sales/sales_layoutB_us_formats.xlsx", options={"date_order": "DMY"})
    assert results[2].normalized and results[2].normalized["invoice_date"] == "2026-12-06"


def test_purchases_vendor_credit_negative() -> None:
    lk = Lookups(
        products={"BRK-3P-100A": ("prod-brk", "mfr-volt")},
        manufacturers={"VOLT": "mfr-volt", "BRIT": "mfr-brit"},
    )
    results, _, _ = run("purchases/purchases_VOLT_2026.csv", "purchases", lk)
    assert len(results) == 10 and all(r.status == "valid" for r in results), [codes(r) for r in results]
    wrong = RowValidator("purchases", {f.name: f.name for f in FIELDS["purchases"]}, {}, lk).validate(
        2,
        {"document_number": "R1", "line_number": "1", "doc_type": "", "document_date": "2026-01-01",
         "manufacturer_code": "BRIT", "po_number": "", "sku": "BRK-3P-100A", "quantity": "1", "uom": "EA",
         "unit_cost": "1", "extended_amount": "1", "currency_code": ""},
    )  # fmt: skip
    assert codes(wrong) == ["product_manufacturer_mismatch"]
    vc = results[-1].normalized
    assert vc and vc["doc_type"] == "vendor_credit" and vc["quantity"] == "-200.000000"
    assert vc["extended_amount"] == "-20000.0000" and vc["manufacturer_id"] == "mfr-volt"


def test_master_files_validate() -> None:
    cust, _, _ = run("master/customers.csv", "customers")
    assert len(cust) == 7 and all(r.status == "valid" for r in cust)
    mfrs = Lookups(manufacturers={"VOLT": "a", "BRIT": "b", "COPR": "c", "NWND": "d"})
    prods, _, _ = run("master/products.csv", "products", mfrs)
    assert len(prods) == 10 and all(r.status == "valid" for r in prods), [codes(r) for r in prods]
    assert prods[4].normalized and prods[4].normalized["sku"] == "00418"
    uom, _, _ = run(
        "master/uom_conversions.csv", "uom", Lookups(products=PRODUCTS | {"WIRE-10-3-RL": ("p10", "m")})
    )
    assert [u.normalized["factor"] for u in uom if u.normalized] == ["500.00000000", "250.00000000"]


def _validator(import_type: str = "sales", lk: Lookups | None = None) -> RowValidator:
    mapping: dict[str, str | None] = {f.name: f.name for f in FIELDS[import_type]}
    return RowValidator(import_type, mapping, {}, lk or lookups())


def _row(**kw: str) -> dict[str, str]:
    base = {f.name: "" for f in FIELDS["sales"]}
    base.update(
        document_number="INV-1", line_number="1", invoice_date="2026-06-10", customer_code="ALDER", sku="BRK-3P-100A",
        quantity="10", uom="EA", unit_price="88.00", extended_amount="880.00",
    )  # fmt: skip
    base.update(kw)
    return base


def test_conflicting_duplicate_and_header_consistency() -> None:
    v = _validator()
    assert v.validate(2, _row()).status == "valid"
    conflict = v.validate(3, _row(quantity="11", extended_amount="968"))
    assert conflict.status == "error" and codes(conflict) == ["duplicate_key_conflict"]
    other_customer = v.validate(4, _row(line_number="2", customer_code="BLUFF"))
    assert codes(other_customer) == ["inconsistent_document_header"]


def test_unknown_customer_currency_negative_price_and_orphan_credit_note() -> None:
    v = _validator(lk=lookups(sales_line_exists=lambda keys: {("INV-OLD", 1)}))
    assert codes(v.validate(2, _row(customer_code="ZZZ"))) == ["unknown_customer"]
    assert codes(v.validate(3, _row(document_number="INV-2", currency_code="EUR"))) == ["currency_mismatch"]
    assert codes(v.validate(4, _row(document_number="INV-3", unit_price="-1"))) == ["negative_price"]
    on_invoice = v.validate(
        5, _row(document_number="INV-4", original_document_number="INV-1", original_line_number="1")
    )
    assert codes(on_invoice) == ["original_on_invoice"]
    cn_db = v.validate(
        6,
        _row(
            document_number="CN-1",
            doc_type="CN",
            original_document_number="INV-OLD",
            original_line_number="1",
        ),
    )
    cn_missing = v.validate(
        7,
        _row(
            document_number="CN-2",
            doc_type="CN",
            original_document_number="INV-404",
            original_line_number="1",
        ),
    )
    assert cn_db.status == cn_missing.status == "valid"
    warnings = v.finish()
    assert list(warnings) == [7] and warnings[7][0].code == "original_line_not_found"
    assert (
        warnings[7][0].severity == "warning"
    )  # loads without a link; the engine flags return_original_missing
    assert v.unmapped[("customer", "ZZZ")].count == 1


def test_pan_guard_marker_rejects_the_row() -> None:
    v = _validator()
    r = v.validate(2, {**_row(), "\x1fpan": "description"})
    assert codes(r) == ["possible_card_number"]


def test_row_hash_ignores_number_formatting_but_not_values() -> None:
    a = _validator().validate(2, _row(unit_price="88"))
    b = _validator().validate(2, _row(unit_price="88.000"))
    c = _validator().validate(2, _row(unit_price="87"))
    assert a.row_hash == b.row_hash != c.row_hash


def test_mapping_checks() -> None:
    headers = ["invoice_no", "qty"]
    with pytest.raises(MappingInvalid) as err:
        check_mapping(
            "sales", {"quantity": "qty", "bogus": "qty", "sku": "nope"}, {"date_order": "YMD"}, headers
        )
    found = {(d["field"], d["code"]) for d in err.value.details}
    assert ("mapping.bogus", "unknown_field") in found and ("mapping.sku", "unknown_column") in found
    assert ("mapping.document_number", "required") in found and ("options.date_order", "invalid") in found
    full = {f.name: None for f in FIELDS["uom"]} | {"from_uom": "a", "to_uom": "b", "factor": "c"}
    check_mapping("uom", full, {}, ["a", "b", "c"])  # sku is optional (tenant default conversion)


def test_customer_parents() -> None:
    v = _validator("customers", Lookups(customers={"ROOT": "r"}))

    def c(code: str, parent: str = "") -> dict[str, str]:
        return {
            "customer_code": code,
            "customer_name": code,
            "customer_type": "",
            "parent_customer_code": parent,
            "external_id": "",
            "active": "",
        }

    assert v.validate(2, c("KID", "MID")).phase == 2
    assert v.validate(3, c("MID", "ROOT")).phase == 2
    assert v.validate(4, c("ORPHAN", "NOBODY")).status == "valid"
    assert codes(v.validate(5, c("SELF", "SELF"))) == ["own_parent"]
    out = v.finish()
    assert out[2][0].code == "parent_after_child" and out[4][0].code == "unknown_parent"


def test_card_number_in_a_mapping_default_is_refused() -> None:
    with pytest.raises(MappingInvalid) as err:
        check_mapping(
            "customers",
            {"customer_code": "c", "customer_name": "n"},
            {"defaults": {"external_id": "4111 1111 1111 1111"}},
            ["c", "n"],
        )
    assert ("options.defaults.external_id", "card_number") in {
        (d["field"], d["code"]) for d in err.value.details
    }
    check_mapping(
        "customers",
        {"customer_code": "c", "customer_name": "n"},
        {"defaults": {"external_id": "X1"}},
        ["c", "n"],
    )
