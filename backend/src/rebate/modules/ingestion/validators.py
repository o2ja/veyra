"""Canonical fields per import type, mapping checks, and row validation (pure; FR-IM-02..05, -08, -09).

A *mapping* is ``{canonical_field: source_header | None}``; *options* carry the transforms that apply to
the whole file: ``date_order`` (``MDY`` default for the US electrical vertical, or ``DMY``),
``decimal_separator`` (``.``/``,``), ``delimiter``, ``header_row``, ``sheet`` and ``defaults``
(``{field: constant}`` used when a field is unmapped or a cell is blank, e.g. ``doc_type``).

``RowValidator`` turns one raw row into a normalised row (JSON-safe values, resolved master-data ids)
plus row errors/warnings. Lookups are passed in, so this module stays free of the database. Cross-row
rules (in-file duplicates, document header consistency, credit-note originals) are tracked across calls
in file order and finished by ``finish()``.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from rebate.modules.ingestion import normalizers as n
from rebate.modules.ingestion.readers import EXTRA_CELLS

ImportType = Literal["sales", "purchases", "customers", "products", "uom"]
IMPORT_TYPES: tuple[ImportType, ...] = ("sales", "purchases", "customers", "products", "uom")
PAN_MARK = "\x1fpan"  # raw-row key listing columns whose card-like content was removed at staging

Kind = Literal["text", "code", "sku", "int", "date", "qty", "price", "amount", "factor", "uom", "doc_type",
               "currency", "bool", "customer_type"]  # fmt: skip


@dataclass(frozen=True)
class Field:
    name: str
    kind: Kind
    required: bool = False
    aliases: tuple[str, ...] = ()
    max_length: int = 100


def _f(name: str, kind: Kind, required: bool = False, *aliases: str, max_length: int = 100) -> Field:
    return Field(name, kind, required, (name, *aliases), max_length)


FIELDS: dict[str, tuple[Field, ...]] = {
    "sales": (
        _f(
            "document_number",
            "text",
            True,
            "invoice_no",
            "invoice_number",
            "invoice",
            "inv #",
            "inv no",
            "doc_no",
        ),
        _f("line_number", "int", True, "line_no", "line", "ln", "line #"),
        _f("doc_type", "doc_type", False, "type", "document_type"),
        _f("invoice_date", "date", True, "inv date", "invoice date", "document_date", "date"),
        _f("ship_date", "date", False, "ship date", "shipped"),
        _f(
            "customer_code",
            "code",
            True,
            "customer",
            "sold-to",
            "sold to",
            "cust #",
            "customer_id",
            "customer code",
        ),
        _f("customer_name", "text", False, "customer name", max_length=200),
        _f("branch_code", "code", False, "branch", "branch code"),
        _f("sku", "sku", True, "item", "item #", "product", "part number", "item number"),
        _f("description", "text", False, "desc", "item description", max_length=500),
        _f("quantity", "qty", True, "qty", "qty shipped", "quantity shipped"),
        _f("uom", "uom", True, "um", "unit", "unit of measure"),
        _f("unit_price", "price", True, "price", "net price", "sell price"),
        _f("unit_cost", "price", False, "cost", "std cost", "standard cost"),
        _f("replacement_cost", "price", False, "repl cost", "replacement cost"),
        _f("extended_amount", "amount", True, "ext_amount", "ext net", "extended", "amount", "ext amount"),
        _f("spa_reference", "text", False, "spa_ref", "spa #", "spa", "spa number"),
        _f("project_reference", "text", False, "project_ref", "project", "job"),
        _f("original_document_number", "text", False, "orig_invoice_no", "original invoice", "orig invoice"),
        _f("original_line_number", "int", False, "orig_line_no", "original line", "orig line"),
        _f("currency_code", "currency", False, "currency", "curr"),
    ),
    "purchases": (
        _f("document_number", "text", True, "doc_no", "receipt_no", "document number", "doc #"),
        _f("line_number", "int", True, "line_no", "line", "ln"),
        _f("doc_type", "doc_type", False, "type", "document_type"),
        _f("document_date", "date", True, "doc_date", "date", "receipt date"),
        _f("manufacturer_code", "code", True, "manufacturer", "vendor", "vendor code", "mfr"),
        _f("po_number", "text", False, "po_no", "po", "po #"),
        _f("sku", "sku", True, "item", "item #", "product"),
        _f("quantity", "qty", True, "qty", "qty received"),
        _f("uom", "uom", True, "um", "unit"),
        _f("unit_cost", "price", True, "cost", "unit cost"),
        _f("extended_amount", "amount", True, "ext_amount", "extended", "amount"),
        _f("currency_code", "currency", False, "currency"),
    ),
    "customers": (
        _f("customer_code", "code", True, "code", "customer", "customer id"),
        _f("customer_name", "text", True, "name", "customer name", max_length=200),
        _f("customer_type", "customer_type", False, "type"),
        _f("parent_customer_code", "code", False, "parent", "parent code", "parent_code"),
        _f("external_id", "text", False, "external id"),
        _f("active", "bool", False),
    ),
    "products": (
        _f("sku", "sku", True, "item", "product"),
        _f("description", "text", False, "desc", max_length=500),
        _f("manufacturer_code", "code", True, "manufacturer", "mfr", "vendor"),
        _f("base_uom", "uom", True, "uom", "um", "unit"),
        _f("category", "text", False),
        _f("manufacturer_part_number", "text", False, "mpn", "mfr part", "manufacturer part"),
        _f("list_cost", "price", False, "cost", "list cost"),
        _f("active", "bool", False),
    ),
    "uom": (
        _f("sku", "sku", False, "item", "product"),
        _f("from_uom", "uom", True, "from"),
        _f("to_uom", "uom", True, "to"),
        _f("factor", "factor", True, "multiplier", "conversion"),
    ),
}

# NUMERIC columns: (scale, integer digits)
_NUMERIC = {"qty": (6, 12), "price": (6, 12), "amount": (4, 14), "factor": (8, 10)}
_TRUE, _FALSE = {"TRUE", "YES", "Y", "1", "ACTIVE"}, {"FALSE", "NO", "N", "0", "INACTIVE"}
CUSTOMER_TYPES = ("contractor", "end_user", "branch", "other")
DEFAULT_OPTIONS: dict[str, Any] = {"date_order": "MDY", "decimal_separator": "."}


class MappingInvalid(ValueError):
    def __init__(self, details: list[dict[str, str]]) -> None:
        super().__init__("mapping invalid")
        self.details = details


def suggest_mapping(import_type: str, headers: list[str]) -> dict[str, str | None]:
    """Deterministic suggestion by header synonyms (no AI). The user confirms it (FR-IM-03)."""
    by_name = {n.normalise_header(h): h for h in headers}
    return {f.name: next((by_name[a] for a in f.aliases if a in by_name), None) for f in FIELDS[import_type]}


def check_mapping(
    import_type: str, mapping: dict[str, Any], options: dict[str, Any], headers: list[str]
) -> None:
    known = {f.name: f for f in FIELDS[import_type]}
    defaults = options.get("defaults") or {}
    details: list[dict[str, str]] = []
    for key, column in mapping.items():
        if key not in known:
            details.append({"field": f"mapping.{key}", "code": "unknown_field", "message": "Unknown field."})
        elif column is not None and column not in headers:
            details.append(
                {"field": f"mapping.{key}", "code": "unknown_column", "message": "No such column."}
            )
    if not isinstance(defaults, dict) or any(
        k not in known or not isinstance(v, str) for k, v in defaults.items()
    ):
        details.append(
            {"field": "options.defaults", "code": "invalid", "message": "Defaults must map fields to text."}
        )
        defaults = {}
    for f in FIELDS[import_type]:
        if f.required and mapping.get(f.name) is None and f.name not in defaults:
            details.append({"field": f"mapping.{f.name}", "code": "required", "message": "Map this field."})
    if options.get("date_order", "MDY") not in ("MDY", "DMY"):
        details.append({"field": "options.date_order", "code": "invalid", "message": "Use MDY or DMY."})
    if options.get("decimal_separator", ".") not in (".", ","):
        details.append({"field": "options.decimal_separator", "code": "invalid", "message": "Use . or ,"})
    if details:
        raise MappingInvalid(details)


@dataclass(frozen=True)
class Issue:
    column: str | None
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass
class Lookups:
    """Master data the validator resolves against (codes/SKUs/aliases are canonical upper case)."""

    customers: dict[str, str] = field(default_factory=dict)  # code or alias -> customer id
    products: dict[str, tuple[str, str]] = field(
        default_factory=dict
    )  # sku or alias -> (id, manufacturer id)
    branches: dict[str, str] = field(default_factory=dict)
    manufacturers: dict[str, str] = field(default_factory=dict)
    currency: str = "USD"
    # (source_system, document_number, line_number) -> exists as a current sales line (credit-note originals)
    sales_line_exists: Callable[[list[tuple[str, int]]], set[tuple[str, int]]] = lambda keys: set()


@dataclass
class Validated:
    row_number: int
    status: Literal["valid", "error", "duplicate"]
    normalized: dict[str, Any] | None
    natural_key: str | None
    row_hash: str | None
    phase: int
    issues: list[Issue]


@dataclass
class Unmapped:
    count: int = 0
    example_name: str | None = None


def canonical_json(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_hash(values: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(values).encode()).hexdigest()


# Fields excluded from the row hash: resolved ids depend on master data, not on the source row.
_ID_FIELDS = ("customer_id", "product_id", "manufacturer_id", "branch_id", "parent_customer_id")


class RowValidator:
    def __init__(
        self,
        import_type: str,
        mapping: dict[str, str | None],
        options: dict[str, Any],
        lookups: Lookups,
    ) -> None:
        self.import_type = import_type
        self.fields = FIELDS[import_type]
        self.mapping = mapping
        self.defaults: dict[str, str] = dict(options.get("defaults") or {})
        self.date_order = options.get("date_order", "MDY")
        self.number = n.NumberFormat(options.get("decimal_separator", "."))
        self.lookups = lookups
        self.seen: dict[str, tuple[int, str]] = {}  # natural key -> (row, hash)
        self.headers: dict[tuple[str, str], tuple[int, tuple[Any, ...]]] = {}
        self.file_keys: set[tuple[str, int]] = set()  # invoice (number, line) keys in this file
        self.originals: dict[int, tuple[str, int]] = {}  # credit-note row -> original key
        self.parents: dict[int, str] = {}  # customer row -> parent code
        self.file_codes: dict[str, tuple[int, bool]] = {}  # customer code -> (row, has a parent)
        self.unmapped: dict[tuple[str, str], Unmapped] = {}

    # -- one row ----------------------------------------------------------------------------------
    def _cell(self, raw: dict[str, str], f: Field) -> str:
        column = self.mapping.get(f.name)
        value = raw.get(column, "") if column is not None else ""
        return value if value.strip() else self.defaults.get(f.name, "")

    def _value(self, f: Field, text: str) -> Any:
        if not text.strip():
            if f.required:
                raise n.NormError("required", "A value is required.")
            return None
        match f.kind:
            case "text":
                return n.text_value(text, max_length=f.max_length)
            case "code" | "sku":
                return n.canonical_code(text, max_length=f.max_length)
            case "int":
                return n.parse_int(text)
            case "date":
                return n.parse_date(text, self.date_order).isoformat()
            case "qty" | "price" | "amount" | "factor":
                scale, digits = _NUMERIC[f.kind]
                return n.parse_decimal(text, self.number, scale=scale, int_digits=digits)
            case "uom":
                return n.canonical_uom(text)
            case "doc_type":
                table = n.SALES_DOC_TYPES if self.import_type == "sales" else n.PURCHASE_DOC_TYPES
                return n.canonical_doc_type(text, table)
            case "currency":
                return n.canonical_currency(text)
            case "bool":
                up = text.strip().upper()
                if up in _TRUE or up in _FALSE:
                    return up in _TRUE
                raise n.NormError("invalid_boolean", "Use TRUE or FALSE.")
            case "customer_type":
                value = "_".join(text.strip().lower().split())
                if value not in CUSTOMER_TYPES:
                    raise n.NormError("invalid_customer_type", f"Use one of {', '.join(CUSTOMER_TYPES)}.")
                return value
        raise AssertionError(f.kind)  # pragma: no cover

    def validate(self, row_number: int, raw: dict[str, str]) -> Validated:
        issues: list[Issue] = []
        values: dict[str, Any] = {}
        for f in self.fields:
            column = self.mapping.get(f.name) or f.name
            try:
                values[f.name] = self._value(f, self._cell(raw, f))
            except n.NormError as exc:
                issues.append(Issue(column, _required_code(f, exc.code), exc.message))
                values[f.name] = None
        if raw.get(EXTRA_CELLS):
            issues.append(Issue(None, "too_many_cells", "The row has more cells than the header row."))
        for column in raw.get(PAN_MARK, "").split("\x1f"):
            if column:
                issues.append(
                    Issue(column, "possible_card_number", "Looks like a payment card number; it was removed.")
                )
        if not any(i.severity == "error" for i in issues):
            getattr(self, f"_rules_{self.import_type}")(row_number, values, issues)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            return Validated(row_number, "error", None, None, None, 1, issues)
        normalized = {k: _jsonable(v, k, self.fields) for k, v in values.items()}
        hashed = {k: v for k, v in normalized.items() if k not in _ID_FIELDS}
        digest = row_hash(hashed)
        key = self._natural_key(normalized)
        phase = self._phase(normalized)
        if key in self.seen:
            first_row, first_hash = self.seen[key]
            if first_hash == digest:
                return Validated(row_number, "duplicate", normalized, key, digest, phase, issues)
            issues.append(
                Issue(None, "duplicate_key_conflict", f"Same key as row {first_row} but different values.")
            )
            return Validated(row_number, "error", None, None, None, 1, issues)
        self.seen[key] = (row_number, digest)
        return Validated(row_number, "valid", normalized, key, digest, phase, issues)

    def _natural_key(self, v: dict[str, Any]) -> str:
        if self.import_type in ("sales", "purchases"):
            return f"{v['document_number']}\x1f{v['line_number']}"
        if self.import_type == "customers":
            return str(v["customer_code"])
        if self.import_type == "products":
            return str(v["sku"])
        return f"{v['sku'] or ''}\x1f{v['from_uom']}\x1f{v['to_uom']}"

    def _phase(self, v: dict[str, Any]) -> int:
        """Load order: credit notes after invoices (originals exist), child customers after parents."""
        if self.import_type == "sales":
            return 2 if v["doc_type"] == "credit_note" else 1
        if self.import_type == "customers":
            return 2 if v["parent_customer_code"] else 1
        return 1

    # -- per-type rules ---------------------------------------------------------------------------
    def _unmapped(self, kind: str, value: str, name: str | None) -> None:
        entry = self.unmapped.setdefault((kind, value), Unmapped())
        entry.count += 1
        entry.example_name = entry.example_name or name

    def _currency(self, v: dict[str, Any], issues: list[Issue]) -> None:
        v["currency_code"] = v["currency_code"] or self.lookups.currency
        if v["currency_code"] != self.lookups.currency:
            issues.append(
                Issue(
                    "currency_code",
                    "currency_mismatch",
                    f"Only {self.lookups.currency} is supported (ADR-024).",
                )
            )

    def _signed(self, negative: bool, v: dict[str, Any], issues: list[Issue]) -> None:
        try:
            v["quantity"], v["extended_amount"] = n.canonical_sign(
                negative, v["quantity"], v["extended_amount"]
            )
        except n.NormError as exc:
            issues.append(Issue("quantity", exc.code, exc.message))

    def _product(self, v: dict[str, Any], issues: list[Issue], name: str | None) -> None:
        found = self.lookups.products.get(v["sku"])
        if found is None:
            issues.append(
                Issue("sku", "unknown_product", "Unknown product; resolve it in the unmapped-entities queue.")
            )
            self._unmapped("product", v["sku"], name)
        else:
            v["product_id"], v["manufacturer_id"] = found

    def _rules_sales(self, row: int, v: dict[str, Any], issues: list[Issue]) -> None:
        v["doc_type"] = v["doc_type"] or "invoice"
        self._signed(v["doc_type"] == "credit_note", v, issues)
        for price in ("unit_price", "unit_cost", "replacement_cost"):
            if v[price] is not None and v[price] < 0:
                issues.append(Issue(price, "negative_price", "Prices and costs must not be negative."))
        self._currency(v, issues)
        customer = self.lookups.customers.get(v["customer_code"])
        if customer is None:
            issues.append(
                Issue(
                    "customer_code",
                    "unknown_customer",
                    "Unknown customer; resolve it in the unmapped-entities queue.",
                )
            )
            self._unmapped("customer", v["customer_code"], v["customer_name"])
        v["customer_id"] = customer
        self._product(v, issues, v["description"])
        v["branch_id"] = None
        if v["branch_code"] is not None:
            v["branch_id"] = self.lookups.branches.get(v["branch_code"])
            if v["branch_id"] is None:
                issues.append(
                    Issue(
                        "branch_code", "unknown_branch", "Unknown branch; loaded without a branch.", "warning"
                    )
                )
        has_orig = v["original_document_number"] is not None or v["original_line_number"] is not None
        if has_orig and v["doc_type"] != "credit_note":
            issues.append(
                Issue(
                    "original_document_number",
                    "original_on_invoice",
                    "Only credit notes reference an original invoice.",
                )
            )
        elif has_orig and (v["original_document_number"] is None or v["original_line_number"] is None):
            issues.append(
                Issue(
                    "original_line_number",
                    "original_incomplete",
                    "Give both the original invoice number and line.",
                )
            )
        if any(i.severity == "error" for i in issues):
            return
        header = (v["doc_type"], v["invoice_date"], v["customer_code"], v["branch_code"], v["currency_code"])
        doc_key = (v["doc_type"], v["document_number"])
        first = self.headers.setdefault(doc_key, (row, header))
        if first[1] != header:
            issues.append(
                Issue(
                    None,
                    "inconsistent_document_header",
                    f"Date, customer, branch or currency differ from row {first[0]} of the same document.",
                )
            )
            return
        if v["doc_type"] == "invoice":
            self.file_keys.add((v["document_number"], v["line_number"]))
        elif has_orig:
            self.originals[row] = (v["original_document_number"], v["original_line_number"])

    def _rules_purchases(self, row: int, v: dict[str, Any], issues: list[Issue]) -> None:
        v["doc_type"] = v["doc_type"] or "receipt"
        self._signed(v["doc_type"] == "vendor_credit", v, issues)
        if v["unit_cost"] is not None and v["unit_cost"] < 0:
            issues.append(Issue("unit_cost", "negative_price", "Costs must not be negative."))
        self._currency(v, issues)
        v["manufacturer_id"] = self.lookups.manufacturers.get(v["manufacturer_code"])
        if v["manufacturer_id"] is None:
            issues.append(
                Issue(
                    "manufacturer_code",
                    "unknown_manufacturer",
                    "Unknown manufacturer code; create the manufacturer first.",
                )
            )
            return
        expected = v["manufacturer_id"]
        self._product(v, issues, None)
        if v.get("product_id") is None:
            v["product_id"] = None
        elif v["manufacturer_id"] != expected:
            issues.append(
                Issue(
                    "sku", "product_manufacturer_mismatch", "The product belongs to a different manufacturer."
                )
            )
        v["manufacturer_id"] = expected
        if any(i.severity == "error" for i in issues):
            return
        header = (
            v["doc_type"],
            v["document_date"],
            v["manufacturer_code"],
            v["po_number"],
            v["currency_code"],
        )
        first = self.headers.setdefault((v["doc_type"], v["document_number"]), (row, header))
        if first[1] != header:
            issues.append(
                Issue(
                    None,
                    "inconsistent_document_header",
                    f"Header fields differ from row {first[0]} of the same document.",
                )
            )

    def _rules_customers(self, row: int, v: dict[str, Any], issues: list[Issue]) -> None:
        v["customer_type"] = v["customer_type"] or "other"
        v["active"] = True if v["active"] is None else v["active"]
        self.file_codes[v["customer_code"]] = (row, v["parent_customer_code"] is not None)
        if v["parent_customer_code"] == v["customer_code"]:
            issues.append(Issue("parent_customer_code", "own_parent", "A customer cannot be its own parent."))
        elif v["parent_customer_code"] is not None:
            self.parents[row] = v["parent_customer_code"]

    def _rules_products(self, row: int, v: dict[str, Any], issues: list[Issue]) -> None:
        v["active"] = True if v["active"] is None else v["active"]
        v["manufacturer_id"] = self.lookups.manufacturers.get(v["manufacturer_code"])
        if v["manufacturer_id"] is None:
            issues.append(
                Issue(
                    "manufacturer_code",
                    "unknown_manufacturer",
                    "Unknown manufacturer code; create the manufacturer first.",
                )
            )
        if v["list_cost"] is not None and v["list_cost"] < 0:
            issues.append(Issue("list_cost", "negative_price", "Costs must not be negative."))

    def _rules_uom(self, row: int, v: dict[str, Any], issues: list[Issue]) -> None:
        if v["from_uom"] == v["to_uom"]:
            issues.append(Issue("to_uom", "same_uom", "From and to units must differ."))
        if v["factor"] is not None and v["factor"] <= 0:
            issues.append(Issue("factor", "out_of_range", "The factor must be greater than zero."))
        v["product_id"] = None
        if v["sku"] is not None:
            found = self.lookups.products.get(v["sku"])
            if found is None:
                issues.append(
                    Issue(
                        "sku",
                        "unknown_product",
                        "Unknown product; resolve it in the unmapped-entities queue.",
                    )
                )
                self._unmapped("product", v["sku"], None)
            else:
                v["product_id"] = found[0]

    # -- cross-row rules, after the last row ------------------------------------------------------
    def finish(self) -> dict[int, list[Issue]]:
        """Issues that need the whole file: unresolved credit-note originals (warning: the line loads
        without a link and the engine reports ``return_original_missing``) and unknown parents."""
        out: dict[int, list[Issue]] = {}
        if self.originals:
            missing = {k for k in self.originals.values() if k not in self.file_keys}
            in_db = self.lookups.sales_line_exists(sorted(missing)) if missing else set()
            for row, key in self.originals.items():
                if key not in self.file_keys and key not in in_db:
                    out.setdefault(row, []).append(
                        Issue("original_document_number", "original_line_not_found",
                              f"Original invoice line {key[0]}/{key[1]} was not found; loaded without a link.", "warning")
                    )  # fmt: skip
        for row, parent in self.parents.items():
            in_file = self.file_codes.get(parent)
            if in_file is None and parent not in self.lookups.customers:
                out.setdefault(row, []).append(
                    Issue("parent_customer_code", "unknown_parent", "The parent customer does not exist.")
                )
            elif in_file is not None and in_file[1] and in_file[0] > row:
                out.setdefault(row, []).append(
                    Issue("parent_customer_code", "parent_after_child",
                          "List a parent that has its own parent before its children.")
                )  # fmt: skip
        return out


def _required_code(f: Field, code: str) -> str:
    if code == "required" and f.name == "customer_code":
        return "missing_customer"
    if code == "required":
        return f"missing_{f.name}"
    return code


def _jsonable(value: Any, name: str, fields: tuple[Field, ...]) -> Any:
    if isinstance(value, Decimal):
        kind = next(f.kind for f in fields if f.name == name)
        return n.decimal_text(value, _NUMERIC[kind][0])
    return value
