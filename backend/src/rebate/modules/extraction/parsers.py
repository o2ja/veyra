"""Deterministic value parsers for evidence checks (AI_PROCESSING.md §4.4, §5). English documents only (§12).

Everything numeric is ``Decimal``; floats are rejected (BUSINESS_LOGIC.md §1.2). The parsers read the *source*
text (never the model's quote) and return every value they can derive; the verifier checks membership.

Normalisation (documented contract for evidence matching): per code point NFKC + casefold, typographic
quotes/dashes/spaces mapped to ASCII, control and format characters (Cc/Cf, e.g. zero-width) dropped, every
whitespace run collapsed to one space, leading/trailing whitespace removed. ``normalise`` also returns, for
each normalised character, the index of the source character it came from.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal

from rebate.core.money import to_decimal

# typographic quotes, dashes and the no-break space -> ASCII (code points, to keep the source ASCII)
_TRANSLATE = {
    **dict.fromkeys((0x2018, 0x2019, 0x201A), "'"),
    **dict.fromkeys((0x201C, 0x201D, 0x201E), '"'),
    **dict.fromkeys((0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212), "-"),
    0x00A0: " ",
}


def normalise(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    index: list[int] = []
    pending_space = False
    for i, ch in enumerate(text):
        ch = _TRANSLATE.get(ord(ch), ch)
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if c.isspace():
                pending_space = bool(out)
                continue
            if unicodedata.category(c) in ("Cc", "Cf"):
                continue
            if pending_space:
                out.append(" ")
                index.append(i)
                pending_space = False
            out.append(c)
            index.append(i)
    return "".join(out), index


def norm(text: str) -> str:
    return normalise(text)[0]


# ---- numbers, money, quantities -------------------------------------------------------------------

_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def numbers(text: str) -> list[Decimal]:
    """Free-standing numbers (``$1,000,000``, ``(12.50)``, ``-3``); digits inside identifiers are skipped.

    ``SPA-VOLT-2026-884``, ``BRK-3P-100A`` and ``1,00`` (malformed grouping) yield nothing.
    """
    found: list[Decimal] = []
    for m in _NUMBER.finditer(text):
        s, e = m.start(), m.end()
        after = text[e : e + 2]
        if after[:1].isalnum() or after[:1] == "_" or (after[:1] in ",." and after[1:2].isdigit()):
            continue
        j = s
        if j > 0 and text[j - 1] == "$":
            j -= 1
        elif j > 1 and text[j - 1] == " " and text[j - 2] == "$":
            j -= 2
        before = text[j - 1] if j > 0 else ""
        if before and (before.isalnum() or before in "._,"):
            continue
        negative = False
        if before == "-":
            if j > 1 and (text[j - 2].isalnum() or text[j - 2] in "-_."):
                continue  # identifier such as VOLT-2026
            negative = True
        elif before == "(" and text[e : e + 1] == ")":
            negative = True
        value = Decimal(m.group().replace(",", ""))
        found.append(-value if negative else value)
    return found


def parse_decimal(value: object) -> Decimal:
    """A model-supplied number (money, rate, quantity): decimal string/int only; floats rejected."""
    if isinstance(value, str):
        value = value.strip().replace(",", "")
    return to_decimal(value)


_PERCENT = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?(?:%|percent\b|per cent\b)")


def percents(text: str) -> list[Decimal]:
    """``2.5%`` / ``2.5 percent`` → ``Decimal('0.025')`` (fractions, BUSINESS_LOGIC naming convention)."""
    return [Decimal(m.group(1)) / 100 for m in _PERCENT.finditer(text)]


# ---- dates ----------------------------------------------------------------------------------------

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_MDY_NAME = re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.IGNORECASE)
_DMY_NAME = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(\d{4})\b", re.IGNORECASE)
_ISO = re.compile(r"(?<![\w-])(\d{4})-(\d{2})-(\d{2})(?![\w-])")
_NUMERIC = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
_CALENDAR_YEAR = re.compile(r"\bcalendar year (\d{4})\b", re.IGNORECASE)


def _date(y: str, m: int | str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def dates(text: str) -> list[tuple[date, bool]]:
    """Dates stated in the text, each with ``ambiguous``.

    Numeric ``MM/DD/YYYY`` is read US-style (ASSUMPTION: US vertical, OD-1); when both parts are ≤ 12 and
    differ, the reading is ambiguous and the verifier lowers confidence. ``calendar year 2026`` states
    2026-01-01 and 2026-12-31.
    """
    found: list[tuple[date, bool]] = []
    for m in _MDY_NAME.finditer(text):
        d = _date(m.group(3), _MONTHS[m.group(1)[:3].lower()], m.group(2))
        if d:
            found.append((d, False))
    for m in _DMY_NAME.finditer(text):
        d = _date(m.group(3), _MONTHS[m.group(2)[:3].lower()], m.group(1))
        if d:
            found.append((d, False))
    for m in _ISO.finditer(text):
        d = _date(m.group(1), m.group(2), m.group(3))
        if d:
            found.append((d, False))
    for m in _NUMERIC.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        d = _date(m.group(3), a, m.group(2))
        if d:
            found.append((d, a <= 12 and b <= 12 and a != b))
    for m in _CALENDAR_YEAR.finditer(text):
        found += [(date(int(m.group(1)), 1, 1), False), (date(int(m.group(1)), 12, 31), False)]
    return found


def parse_iso_date(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"not an ISO date: {value!r}")
    return date.fromisoformat(value)


# ---- units of measure, identifiers, names ----------------------------------------------------------

_UOM_SYNONYMS = {
    "EA": r"\b(?:ea|each|unit|units|pc|pcs|piece|pieces)\b",
    "FT": r"\b(?:ft|foot|feet)\b",
    "RL": r"\b(?:rl|reel|reels)\b",
}


def uoms(text: str) -> set[str]:
    """Canonical UoM codes named in the text (``per foot`` → ``FT``)."""
    low = norm(text)
    return {code for code, pattern in _UOM_SYNONYMS.items() if re.search(pattern, low)}


def parse_uom(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{1,10}", value.strip()):
        raise ValueError(f"not a unit of measure: {value!r}")
    return value.strip().upper()


def parse_identifier(value: object) -> str:
    """SKU / agreement number: letters, digits and ``-./_`` (no spaces), max 64 chars."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-./_]{0,63}", value.strip()):
        raise ValueError(f"not an identifier: {value!r}")
    return value.strip().upper()


def contains_identifier(text: str, value: str) -> bool:
    """``value`` occurs as a whole token: ``SPA-VOLT-2026-88`` is not found in ``SPA-VOLT-2026-884``."""
    pattern = r"(?<![a-z0-9\-_/])" + re.escape(norm(value)) + r"(?![a-z0-9_/]|-[a-z0-9])"
    return re.search(pattern, norm(text)) is not None


def contains_phrase(text: str, value: str) -> bool:
    """Normalised ``value`` occurs on word boundaries (names: case/whitespace-insensitive)."""
    v = norm(value)
    return bool(v) and re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", norm(text)) is not None


# ---- interpretation fields: keyword tables ----------------------------------------------------------
# A keyword table maps each allowed value to the phrasing that states it. The verifier treats a single
# derived value as "parsed deterministically", a different derived value as a contradiction (unsupported),
# and no/several derived values as "not derivable" (flagged, low confidence). Tables cover common English
# phrasings and grow with the golden set; they never default a value.

KEYWORDS: dict[str, dict[object, str]] = {
    "agreement_type": {
        "spa": r"\bspecial pricing\b|\bship[- ]and[- ]debit\b|\bspa\b",
        "rebate": r"\brebate\b|\bincentive program\b",
    },
    "date_basis": {
        "ship_date": r"\bship(?:ment)?[- ]?date\b|\bdate of shipment\b",
        "invoice_date": r"\binvoice[- ]date\b|\bdate of invoice\b",
    },
    "calculation_basis": {
        "price_difference": r"difference between [^.]{0,60}cost|cost (?:minus|less) [^.]{0,30}cost",
        "flat_per_unit": r"\ballowance\b[^.]{0,30}\bper\b|\bflat (?:amount|allowance|rate) per\b",
        "guaranteed_margin": r"\bguaranteed margin\b",
        "percent_of_sell": r"(?:percent|%) of (?:the )?(?:sell|selling|resale) price",
        "percent_of_cost": r"(?:percent|%) of (?:the )?(?:standard |net )?cost",
    },
    "cost_source": {
        "agreement_reference": r"\bstandard cost\b|\breference cost\b|\binto[- ]stock cost\b",
        "line_cost": r"\b(?:invoice|line|actual) cost\b",
        "replacement_cost": r"\breplacement cost\b",
    },
    "spa_reference_required": {
        True: r"\bmust (?:reference|quote|include|state|show) the (?:spa|agreement)\b",
        False: r"\b(?:need not|not required to|does not need to) (?:reference|quote)\b",
    },
    "tier_mode": {
        "retroactive": r"\bretroactiv|\bfirst dollar\b",
        "incremental": r"\bincremental\b|\bonly (?:to|on) (?:the )?(?:volume|purchases|sales) (?:within|above|in)\b",
    },
    "volume_basis": {
        "purchases": r"\bpurchases\b",
        "sales": r"\bsales\b",
    },
    "spa_lines_policy": {
        "qualify_only": r"\bcounts? toward[^.]*\bbut\b[^.]*\bnot\b[^.]*\bearn",
        "exclude": r"\bexcluded from\b|\bdo(?:es)? not count toward\b",
    },
    "customer_scope": {
        "exclude": r"\bnot eligible\b|\bexclud|\bexcept\b",
        "include": r"(?<!not )\beligible\b|\bapplies only to\b|\bonly (?:to|for)\b|\binclud",
    },
}


def keyword_values(kind: str, text: str) -> set[object]:
    low = norm(text)
    return {value for value, pattern in KEYWORDS[kind].items() if re.search(pattern, low)}


_ROUND_MODE = {
    # "nearest cent" is read as half-up: the BUSINESS_LOGIC.md §1.2 default when an agreement does not
    # name a tie rule (ASSUMPTION, reviewer acknowledges).
    "half_up": r"\bnearest\b|\bhalf[- ]up\b",
    "half_even": r"\bhalf[- ]even\b|\bbanker",
    "down": r"\btruncat|\brounded down\b|\bround down\b",
}
_ROUND_TIMING = {
    "per_line": r"\bper line\b|\beach line\b|\bline by line\b",
    "per_document": r"\bper (?:invoice|document)\b|\binvoice total\b",
}


def rounding_parts(text: str) -> dict[str, set[object]]:
    """Derivable components of a rounding rule: ``mode``, ``scale``, ``timing`` (each a set of readings)."""
    low = norm(text)
    scale: set[object] = set()
    if re.search(r"\bnearest cent\b|\bnearest penny\b", low):
        scale.add(2)
    if re.search(r"\bnearest (?:whole )?dollar\b", low):
        scale.add(0)
    scale |= {int(m.group(1)) for m in re.finditer(r"\b([0-6]) decimal places?\b", low)}
    return {
        "mode": {v for v, p in _ROUND_MODE.items() if re.search(p, low)},
        "scale": scale,
        "timing": {v for v, p in _ROUND_TIMING.items() if re.search(p, low)},
    }


def measurement_periods(text: str) -> list[dict[str, str]]:
    return [
        {"kind": "calendar_year", "start": f"{m.group(1)}-01-01", "end": f"{m.group(1)}-12-31"}
        for m in _CALENDAR_YEAR.finditer(text)
    ]
