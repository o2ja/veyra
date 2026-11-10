"""Build engine inputs straight from the SYNTHETIC testdata (stdlib csv/json).

This is NOT the import pipeline (S3/S5.3): it is a small, explicit test adapter. Ids are business codes:
product_id = SKU, customer_id = customer code, version_id = agreement external number,
line_id = "<document>/<line>".
"""

import csv
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from rebate_engine.models import (
    ManufacturerSettings,
    PriorClaim,
    RebateVersion,
    ReturnOriginal,
    SalesLine,
    SpaVersion,
    UomConversion,
    VolumeLine,
)
from rebate_engine.rules import rebate_version_from_payload, spa_version_from_payload

_BACKEND = Path(__file__).resolve().parents[2]


@cache
def testdata_root() -> Path:
    for candidate in (_BACKEND.parent / "testdata", Path("/testdata")):  # repo checkout, dev container
        if (candidate / "generated").is_dir():
            return candidate / "generated"
    raise FileNotFoundError("testdata/generated not found")


def _csv(rel: str) -> list[dict[str, str]]:
    with (testdata_root() / rel).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ground_truth(name: str) -> Any:
    return json.loads((testdata_root() / "ground_truth" / name).read_text(encoding="utf-8"))


def payload(external_number: str) -> dict[str, Any]:
    path = testdata_root() / "agreements" / "manual_payloads" / f"{external_number}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@cache
def products() -> dict[str, dict[str, str]]:
    return {r["sku"]: r for r in _csv("master/products.csv")}


def customer_codes() -> dict[str, str]:
    return {r["customer_code"]: r["customer_code"] for r in _csv("master/customers.csv")}


def conversions() -> tuple[UomConversion, ...]:
    return tuple(
        UomConversion(
            product_id=r["sku"], from_uom=r["from_uom"], to_uom=r["to_uom"], factor=Decimal(r["factor"])
        )
        for r in _csv("master/uom_conversions.csv")
    )


def manufacturers(**overrides: dict[str, Any]) -> tuple[ManufacturerSettings, ...]:
    out = []
    for m in ground_truth("master_manufacturers.json"):
        settings = ManufacturerSettings(
            manufacturer_id=m["code"],
            conflict_policy=m["conflict_policy"],
            claim_window_basis=m["claim_window_basis"],
            claim_window_days=m["claim_window_days"],
        )
        out.append(replace(settings, **overrides.get(m["code"], {})))
    return tuple(out)


def spa_version(external_number: str, data: dict[str, Any] | None = None) -> SpaVersion:
    data = data or payload(external_number)
    return spa_version_from_payload(
        data,
        version_id=external_number,
        agreement_id=external_number,
        manufacturer_id=data["header"]["manufacturer_code"],
        product_ids={sku: sku for sku in products()},
        customer_ids=customer_codes(),
    )


def rebate_version(external_number: str) -> RebateVersion:
    data = payload(external_number)
    return rebate_version_from_payload(
        data,
        version_id=external_number,
        manufacturer_id=data["header"]["manufacturer_code"],
        product_ids={sku: sku for sku in products()},
    )


SPA_PAYLOADS = ("SPA-BRIT-2026-031", "SPA-COPR-2026-107", "SPA-VOLT-2026-884", "SPA-VOLT-2026-901")


def _opt_dec(value: str) -> Decimal | None:
    return Decimal(value) if value.strip() else None


def sales_lines(rel: str, prior_claims: dict[str, PriorClaim] | None = None) -> tuple[SalesLine, ...]:
    """Sales CSV (layout A) -> SalesLine. CN quantities are negated (import normalisation, §7); returns are
    linked by (orig_invoice_no, orig_line_no); `prior_claims` maps line ids to their frozen claim line."""
    prior_claims = prior_claims or {}
    rows = _csv(rel)
    prods = products()

    def line_id(doc: str, no: str) -> str:
        return f"{doc}/{int(no)}"

    returned: dict[str, Decimal] = {}
    for r in rows:
        if r["doc_type"] == "CN" and r["orig_invoice_no"]:
            key = line_id(r["orig_invoice_no"], r["orig_line_no"])
            returned[key] = returned.get(key, Decimal(0)) + abs(Decimal(r["qty"]))
    originals = {line_id(r["invoice_no"], r["line_no"]): r for r in rows if r["doc_type"] != "CN"}

    out = []
    for r in rows:
        lid = line_id(r["invoice_no"], r["line_no"])
        product = prods.get(r["sku"])
        is_return = r["doc_type"] == "CN"
        qty = Decimal(r["qty"])
        ret_orig = None
        if is_return and r["orig_invoice_no"]:
            oid = line_id(r["orig_invoice_no"], r["orig_line_no"])
            orig = originals.get(oid)
            if orig is not None:
                ret_orig = ReturnOriginal(
                    line_id=oid,
                    quantity=Decimal(orig["qty"]),
                    returned_quantity=returned[oid],
                    prior_claim=prior_claims.get(oid),
                )
        out.append(
            SalesLine(
                line_id=lid,
                document_number=r["invoice_no"],
                line_number=int(r["line_no"]),
                invoice_date=date.fromisoformat(r["invoice_date"]),
                ship_date=date.fromisoformat(r["ship_date"]) if r["ship_date"] else None,
                customer_id=r["customer_code"],
                product_id=r["sku"] if product else None,
                manufacturer_id=product["manufacturer_code"] if product else None,
                base_uom=product["base_uom"] if product else None,
                quantity=-abs(qty) if is_return else qty,
                uom=r["uom"],
                unit_price=Decimal(r["unit_price"]),
                unit_cost=_opt_dec(r["unit_cost"]),
                spa_reference=r["spa_ref"] or None,
                returned_quantity=Decimal(0) if is_return else returned.get(lid, Decimal(0)),
                prior_claim=None if is_return else prior_claims.get(lid),
                return_original=ret_orig,
            )
        )
    return tuple(out)


def sales_volume_lines(rel: str) -> tuple[VolumeLine, ...]:
    out = []
    for r in _csv(rel):
        sign = -1 if r["doc_type"] == "CN" else 1
        out.append(
            VolumeLine(
                line_id=f"{r['invoice_no']}/{int(r['line_no'])}",
                line_date=date.fromisoformat(r["invoice_date"]),
                manufacturer_id=products()[r["sku"]]["manufacturer_code"],
                product_id=r["sku"],
                amount=sign * abs(Decimal(r["ext_amount"])),
                quantity=sign * abs(Decimal(r["qty"])),
                spa_reference=r["spa_ref"] or None,
            )
        )
    return tuple(out)


def purchase_volume_lines() -> tuple[VolumeLine, ...]:
    return tuple(
        VolumeLine(
            line_id=f"{r['doc_no']}/{int(r['line_no'])}",
            line_date=date.fromisoformat(r["doc_date"]),
            manufacturer_id=r["manufacturer_code"],
            product_id=r["sku"],
            amount=Decimal(r["ext_amount"]),  # vendor credits are already negative in the file
            quantity=Decimal(r["qty"]),
        )
        for r in _csv("purchases/purchases_VOLT_2026.csv")
    )
