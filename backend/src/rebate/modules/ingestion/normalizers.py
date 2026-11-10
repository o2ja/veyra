"""Pure value normalisers for imported cells (FR-IM-02, TESTING_STRATEGY.md §11 item 11).

Every cell arrives as text (readers never type values). These functions turn text into typed values or
raise ``NormError`` with a stable code; they never guess and never round silently:

* numbers: ``.`` or ``,`` decimal separator (per mapping option), strict thousands grouping, currency
  symbols, parenthesis and trailing-minus negatives, scientific notation; more decimals than the
  column holds is an error, not a rounding;
* dates: ISO, US ``M/D/YYYY`` (the electrical-vertical default, ADR-019) or ``D/M/YYYY`` per option,
  ISO datetimes at midnight, Excel serial day numbers; two-digit years are rejected as ambiguous;
* SKUs and codes: trimmed and upper-cased (the case rule), leading zeros always kept;
* UoM codes: upper-cased with common synonyms folded (EACH -> EA, FEET -> FT, REEL -> RL, ...);
* credit notes / vendor credits: quantity and extended amount canonicalised to negative
  (BUSINESS_LOGIC.md §7); invoices/receipts must be positive.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from rebate_engine.money import MONEY_CONTEXT


class NormError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NumberFormat:
    decimal_separator: str = "."  # "." or ","

    def __post_init__(self) -> None:
        if self.decimal_separator not in (".", ","):
            raise ValueError("decimal_separator must be '.' or ','")


DEFAULT_FORMAT = NumberFormat()
_CURRENCY = re.compile(r"^[$€£]|[$€£]$|^USD\s*|\s*USD$")
_PLAIN = re.compile(r"^\d+(\.\d+)?([eE][+-]?\d{1,3})?$")


def parse_decimal(text: str, fmt: NumberFormat = DEFAULT_FORMAT, *, scale: int, int_digits: int) -> Decimal:
    """Exact decimal from text. ``scale``/``int_digits``: the NUMERIC column the value must fit."""
    s = text.strip()
    if not s:
        raise NormError("required", "A value is required.")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    s = _CURRENCY.sub("", s).strip()
    if s.endswith("-"):  # "123.45-" (trailing minus, common in ERP exports)
        negative, s = not negative, s[:-1].strip()
    if s.startswith(("-", "+")):
        if s[0] == "-":
            negative = not negative
        s = s[1:].strip()
    s = _canonical_digits(s, fmt.decimal_separator)
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise NormError("invalid_number", "Not a number.") from None
    if not value.is_finite():
        raise NormError("invalid_number", "Not a number.")
    value = -value if negative else value
    exponent = value.normalize(MONEY_CONTEXT).as_tuple().exponent
    if isinstance(exponent, int) and exponent < -scale:
        raise NormError("too_many_decimals", f"At most {scale} decimal places are allowed.")
    if abs(value) >= Decimal(10) ** int_digits:
        raise NormError("out_of_range", "The number is too large.")
    return value + Decimal(0)  # normalise -0 to 0


_EXPONENT = re.compile(r"^(.*?)([eE][+-]?\d{1,3})$")


def _canonical_digits(s: str, decimal_sep: str) -> str:
    """Remove thousands separators (strict groups of three) and use ``.`` as decimal point."""
    thousands = {",", " ", "'", "\u00a0"} if decimal_sep == "." else {".", " ", "'", "\u00a0"}
    mantissa, exponent = s, ""
    if m := _EXPONENT.fullmatch(s):
        mantissa, exponent = m[1], m[2]
    if mantissa.count(decimal_sep) > 1:
        raise NormError("invalid_number", "Not a number.")
    int_part, _, frac = mantissa.partition(decimal_sep)
    seps = {ch for ch in int_part if not ch.isdigit()}
    if seps:
        if len(seps) > 1 or not seps <= thousands:
            raise NormError("invalid_number", "Not a number.")
        groups = int_part.split(seps.pop())
        if not (
            groups[0].isdigit()
            and len(groups[0]) <= 3
            and all(len(g) == 3 and g.isdigit() for g in groups[1:])
        ):
            raise NormError("invalid_number", "Not a number (thousands separators are misplaced).")
        int_part = "".join(groups)
    result = f"{int_part or '0'}.{frac}" if frac else (int_part or "")
    if not _PLAIN.fullmatch(result + exponent):
        raise NormError("invalid_number", "Not a number.")
    return result + exponent


_ISO = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T]00:00(?::00(?:\.0+)?)?)?$")
_SLASHED = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$")
EXCEL_EPOCH = date(1899, 12, 30)  # 1900 date system incl. the Lotus leap-year bug offset


def parse_date(text: str, order: str = "MDY") -> date:
    """``order`` applies only to day/month dates with slashes, dots or dashes (``MDY`` or ``DMY``)."""
    if order not in ("MDY", "DMY"):
        raise ValueError("order must be MDY or DMY")
    s = text.strip()
    if not s:
        raise NormError("required", "A date is required.")
    try:
        if m := _ISO.fullmatch(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _SLASHED.fullmatch(s):
            if len(m[3]) != 4:
                raise NormError("invalid_date", "Use a four-digit year.")
            first, second = int(m[1]), int(m[2])
            month, day = (first, second) if order == "MDY" else (second, first)
            return date(int(m[3]), month, day)
        if s.isdigit() and 20000 <= int(s) <= 80000:  # Excel serial day number (1954-2119)
            return EXCEL_EPOCH + timedelta(days=int(s))
    except ValueError:
        raise NormError("invalid_date", "Not a valid date.") from None
    raise NormError("invalid_date", "Not a valid date.")


def parse_int(text: str, *, minimum: int = 0, maximum: int = 2_000_000_000) -> int:
    s = text.strip()
    if s.endswith(".0"):  # "1.0" from a spreadsheet number cell
        s = s[:-2]
    if not s.isdigit():
        raise NormError("invalid_number", "Not a whole number.")
    value = int(s)
    if not minimum <= value <= maximum:
        raise NormError("out_of_range", f"Must be between {minimum} and {maximum}.")
    return value


def canonical_code(text: str, *, max_length: int = 100) -> str:
    """SKU / customer / branch / manufacturer code: trimmed, upper-case, leading zeros kept."""
    s = " ".join(text.split()).upper()
    if not s:
        raise NormError("required", "A value is required.")
    if len(s) > max_length:
        raise NormError("too_long", f"At most {max_length} characters.")
    return s


UOM_SYNONYMS = {
    "EACH": "EA", "PC": "EA", "PCS": "EA", "PIECE": "EA", "PIECES": "EA", "UNIT": "EA",
    "FEET": "FT", "FOOT": "FT", "LF": "FT", "LFT": "FT",
    "REEL": "RL", "REELS": "RL",
    "CARTON": "CT", "CTN": "CT",
    "BOX": "BX", "BOXES": "BX",
    "CASE": "CS", "CASES": "CS",
    "PACK": "PK", "PKG": "PK",
    "ROLL": "RO", "ROLLS": "RO",
    "METER": "M", "METRE": "M", "METERS": "M", "METRES": "M",
}  # fmt: skip
_UOM = re.compile(r"^[A-Z0-9]{1,10}$")


def canonical_uom(text: str) -> str:
    s = text.strip().upper().rstrip(".")
    if not s:
        raise NormError("required", "A unit of measure is required.")
    s = UOM_SYNONYMS.get(s, s)
    if not _UOM.fullmatch(s):
        raise NormError("invalid_uom", "Not a valid unit of measure code.")
    return s


SALES_DOC_TYPES = {
    "INV": "invoice", "INVOICE": "invoice", "IN": "invoice", "I": "invoice", "SALE": "invoice",
    "CN": "credit_note", "CR": "credit_note", "CM": "credit_note", "CREDIT": "credit_note",
    "CREDIT_NOTE": "credit_note", "CREDIT NOTE": "credit_note", "CREDIT MEMO": "credit_note",
    "RETURN": "credit_note", "RET": "credit_note", "RMA": "credit_note",
}  # fmt: skip
PURCHASE_DOC_TYPES = {
    "RECEIPT": "receipt", "RCV": "receipt", "REC": "receipt", "R": "receipt", "GR": "receipt",
    "VENDOR_CREDIT": "vendor_credit", "VENDOR CREDIT": "vendor_credit", "VC": "vendor_credit",
    "VCR": "vendor_credit", "CREDIT": "vendor_credit", "DEBIT MEMO": "vendor_credit",
}  # fmt: skip


def canonical_doc_type(text: str, table: dict[str, str]) -> str:
    s = " ".join(text.split()).upper()
    if s in table:
        return table[s]
    raise NormError("invalid_doc_type", f"Unknown document type; use one of {sorted(set(table.values()))}.")


def canonical_sign(negative_doc: bool, quantity: Decimal, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Credit notes / vendor credits carry negative quantity and amount whatever sign the file uses;
    invoices / receipts must be positive (BUSINESS_LOGIC.md §7). Quantity 0 is never accepted (price-only
    credits are not supported in Release 1)."""
    if quantity == 0:
        raise NormError("zero_quantity", "Quantity must not be zero.")
    if negative_doc:
        return -abs(quantity), -abs(amount)
    if quantity < 0:
        raise NormError(
            "invoice_negative_quantity", "An invoice or receipt line must have a positive quantity."
        )
    if amount < 0:
        raise NormError(
            "sign_mismatch", "The extended amount of an invoice or receipt line must not be negative."
        )
    return quantity, amount


def canonical_currency(text: str) -> str:
    s = text.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", s):
        raise NormError("invalid_currency", "Use a three-letter currency code.")
    return s


def text_value(text: str, *, max_length: int) -> str | None:
    s = " ".join(text.split())
    if len(s) > max_length:
        raise NormError("too_long", f"At most {max_length} characters.")
    return s or None


def normalise_header(name: str) -> str:
    return " ".join(name.split()).lower()


def header_signature(headers: list[str]) -> str:
    """Order-independent: a mapping refers to columns by name, so reordered exports reuse it."""
    names = sorted(normalise_header(h) for h in headers)
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def decimal_text(value: Decimal, scale: int) -> str:
    """Canonical text form for hashing and staging (fixed scale, so 88 and 88.00 hash alike)."""
    return format(value.quantize(Decimal(1).scaleb(-scale), context=MONEY_CONTEXT), "f")


_DIGITS = re.compile(r"(?<![\d])(?:\d[ -]?){12,18}\d(?![\d])")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def find_card_number(text: str) -> str | None:
    """PAN guard (SECURITY.md §5): a 13-19 digit run (spaces/dashes allowed) that passes Luhn and starts
    like a card network (2-6). Returns the matched text, else None."""
    for m in _DIGITS.finditer(text):
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and digits[0] in "23456" and luhn_ok(digits):
            return m.group(0)
    return None


def mask_card_number(text: str) -> str:
    """Replace every card-like run with ``[card number removed]`` so it is never stored."""
    out = text
    while (hit := find_card_number(out)) is not None:
        out = out.replace(hit, "[card number removed]")
    return out
