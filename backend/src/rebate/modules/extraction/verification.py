"""S6.3 deterministic verification of model output (AI_PROCESSING.md §4.3-§4.5, §6). Pure, no I/O.

Input is a *candidate extraction*: the §5 JSON a model pass returned (each value wrapped with
``evidence = {page | sheet+cell, quote}``), flattened by :func:`flatten` to ``path -> CandidateField``.

Per field, fail closed:
1. A value needs evidence at a location that exists in the document.
2. The normalised quote (``parsers.normalise``) must occur in that page/cell: exactly, or for quotes of
   >= 20 characters fuzzily (ratio >= 0.95) **with identical digits**: fuzziness never forgives a digit.
3. The matched span must not lie in hidden/tiny/off-page text or in an instruction addressed to an AI.
4. The value must be present in, or derivable by a parser from, the *matched source text* (not the
   model's quote); otherwise ``unsupported``. Interpretation fields (enums, rounding) are derived by keyword
   tables (``parsers.KEYWORDS``): only exactly the claimed reading is supported.
Then the two passes are compared (``conflict`` on disagreeing critical fields) and a confidence is derived;
the model's self-reported confidence is never read.

Schema validation of the payload is S4's ``AgreementValidator``/payload schema: pass it as
``schema_validator`` (hook) to :func:`run_verification`; its errors and warnings feed in here.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import IntEnum, StrEnum
from typing import Any

from rebate.modules.extraction import parsers as p
from rebate.modules.extraction.documents import Document, PdfText
from rebate.modules.extraction.injection import Indicator, IndicatorKind, detect, word_flags

FUZZY_MIN_LEN = 20
FUZZY_RATIO = 0.95


class Kind(StrEnum):
    money = "money"
    rate = "rate"
    quantity = "quantity"
    date = "date"
    identifier = "identifier"
    name = "name"
    text = "text"
    uom = "uom"
    enum = "enum"
    rounding = "rounding"
    period = "period"


@dataclass(frozen=True)
class FieldSpec:
    kind: Kind
    critical: bool
    applies_to: frozenset[str] = frozenset({"spa", "rebate"})
    keywords: str | None = None  # parsers.KEYWORDS table for enums
    allowed: frozenset[object] = frozenset()


_SPA = frozenset({"spa"})
_REBATE = frozenset({"rebate"})


def _enum(
    table: str, values: set[object], critical: bool, applies_to: frozenset[str] = _SPA | _REBATE
) -> FieldSpec:
    return FieldSpec(Kind.enum, critical, applies_to, table, frozenset(values))


# Critical = AI_PROCESSING.md §4.5: dates, rates, thresholds, costs, customer scope, exclusions,
# rounding/date basis, claim UoM; plus (owner-approved, S6) calculation_basis, tier_mode, volume_basis.
SPECS: dict[str, FieldSpec] = {
    "header.agreement_type": _enum("agreement_type", {"spa", "rebate"}, False),
    "header.external_number": FieldSpec(Kind.identifier, False),
    "header.manufacturer_name": FieldSpec(Kind.name, False),
    "header.valid_from": FieldSpec(Kind.date, True),
    "header.valid_to": FieldSpec(Kind.date, True),
    "header.date_basis": _enum("date_basis", {"invoice_date", "ship_date"}, True, _SPA),
    "rules.calculation_basis": _enum(
        "calculation_basis",
        {"price_difference", "percent_of_sell", "percent_of_cost", "flat_per_unit", "guaranteed_margin"},
        True,
        _SPA,
    ),
    "rules.cost_source": _enum(
        "cost_source", {"agreement_reference", "line_cost", "replacement_cost"}, False, _SPA
    ),
    "rules.claim_uom": FieldSpec(Kind.uom, True, _SPA),
    "rules.rounding": FieldSpec(Kind.rounding, True),
    "rules.spa_reference_required": _enum("spa_reference_required", {True, False}, False, _SPA),
    "rules.tier_mode": _enum("tier_mode", {"retroactive", "incremental"}, True, _REBATE),
    "rules.volume_basis": _enum("volume_basis", {"purchases", "sales"}, True, _REBATE),
    "rules.spa_lines_policy": _enum(
        "spa_lines_policy", {"include", "qualify_only", "exclude"}, True, _REBATE
    ),
    "rules.measurement_period": FieldSpec(Kind.period, True, _REBATE),
    "customers[].name": FieldSpec(Kind.name, True),
    "customers[].scope": _enum("customer_scope", {"include", "exclude"}, True),
    "items[].part_number": FieldSpec(Kind.identifier, False),
    "items[].reference_cost": FieldSpec(Kind.money, True),
    "items[].deviated_cost": FieldSpec(Kind.money, True),
    "items[].sell_price": FieldSpec(Kind.money, True),
    "items[].rate": FieldSpec(Kind.rate, True),
    "items[].flat_amount": FieldSpec(Kind.money, True),
    "items[].max_quantity": FieldSpec(Kind.quantity, True),
    "items[].uom": FieldSpec(Kind.uom, True),
    "tiers[].lower_bound": FieldSpec(Kind.money, True),
    "tiers[].rate": FieldSpec(Kind.rate, True),
    "exclusions_text[]": FieldSpec(Kind.text, True),
}

_ROUNDING_VALUES = {"mode": {"half_up", "half_even", "down"}, "timing": {"per_line", "per_document"}}


def spec_for(path: str) -> FieldSpec:
    return SPECS[re.sub(r"\[[^\]]*\]", "[]", path)]


# ---- candidate input -------------------------------------------------------------------------------


class CandidateFormatError(ValueError):
    """The model output does not have the §5 shape (the job's one repair attempt gets this message)."""


@dataclass(frozen=True)
class Evidence:
    quote: str
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None


@dataclass(frozen=True)
class CandidateField:
    path: str
    value: Any
    evidence: Evidence | None


def _evidence(raw: object, path: str) -> Evidence | None:
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, Mapping):
        raise CandidateFormatError(f"{path}: evidence must be an object")
    unknown = set(raw) - {"quote", "page", "sheet", "cell"}
    if unknown:
        raise CandidateFormatError(f"{path}: unknown evidence keys {sorted(unknown)}")
    quote, page, sheet, cell = raw.get("quote"), raw.get("page"), raw.get("sheet"), raw.get("cell")
    if not isinstance(quote, str):
        raise CandidateFormatError(f"{path}: evidence.quote must be a string")
    if page is not None and (isinstance(page, bool) or not isinstance(page, int)):
        raise CandidateFormatError(f"{path}: evidence.page must be an integer")
    for name, v in (("sheet", sheet), ("cell", cell)):
        if v is not None and not isinstance(v, str):
            raise CandidateFormatError(f"{path}: evidence.{name} must be a string")
    return Evidence(quote=quote, page=page, sheet=sheet, cell=cell)


def _wrapped(raw: object, path: str) -> CandidateField:
    if not isinstance(raw, Mapping) or "value" not in raw or set(raw) - {"value", "evidence"}:
        raise CandidateFormatError(f"{path}: expected {{value, evidence}}")
    if isinstance(raw["value"], float):
        raise CandidateFormatError(f"{path}: floats are not allowed; numbers must be strings")
    return CandidateField(path, raw["value"], _evidence(raw.get("evidence"), path))


def flatten(extraction: Mapping[str, Any]) -> dict[str, CandidateField]:
    """§5 extraction JSON -> ``{path: CandidateField}``. Unknown keys and wrong shapes are rejected."""
    if not isinstance(extraction, Mapping):
        raise CandidateFormatError("extraction must be an object")
    if extraction.get("schema_version") != 1:
        raise CandidateFormatError("schema_version must be 1")
    allowed = {
        "schema_version",
        "header",
        "rules",
        "customers",
        "items",
        "tiers",
        "exclusions_text",
        "extractor_notes",
    }
    if unknown := set(extraction) - allowed:
        raise CandidateFormatError(f"unknown top-level keys {sorted(unknown)}")
    out: dict[str, CandidateField] = {}
    for section in ("header", "rules"):
        block = extraction.get(section) or {}
        if not isinstance(block, Mapping):
            raise CandidateFormatError(f"{section} must be an object")
        for key, raw in block.items():
            path = f"{section}.{key}"
            if path not in SPECS:
                raise CandidateFormatError(f"unknown field {path}")
            out[path] = _wrapped(raw, path)
    for section in ("customers", "items", "tiers", "exclusions_text"):
        rows = extraction.get(section) or []
        if not isinstance(rows, list):
            raise CandidateFormatError(f"{section} must be a list")
        for i, row in enumerate(rows):
            base = f"{section}[{i}]"
            if not isinstance(row, Mapping):
                raise CandidateFormatError(f"{base} must be an object")
            if section == "exclusions_text":
                if set(row) - {"text", "evidence"} or not isinstance(row.get("text"), str):
                    raise CandidateFormatError(f"{base}: expected {{text, evidence}}")
                out[base] = CandidateField(base, row["text"], _evidence(row.get("evidence"), base))
                continue
            if section == "customers":
                if set(row) - {"name", "scope"}:
                    raise CandidateFormatError(f"{base}: expected {{name, scope}}")
                name = _wrapped(row.get("name"), f"{base}.name")
                out[name.path] = name
                # scope is stated by the same sentence that names the customer (§5 has no own evidence)
                out[f"{base}.scope"] = CandidateField(f"{base}.scope", row.get("scope"), name.evidence)
                continue
            for key, raw in row.items():
                path = f"{base}.{key}"
                if re.sub(r"\[\d+\]", "[]", path) not in SPECS:
                    raise CandidateFormatError(f"unknown field {path}")
                out[path] = _wrapped(raw, path)
    return out


# ---- single-pass evidence verification ---------------------------------------------------------------


class Status(StrEnum):
    verified = "verified"
    unsupported = "unsupported"
    absent = "absent"  # the pass says the document does not state it (null)


@dataclass(frozen=True)
class Span:
    page: int | None
    sheet: str | None
    cell: str | None
    start: int  # offsets into the page text / cell value (source, not normalised)
    end: int
    text: str
    boxes: tuple[tuple[float, float, float, float], ...] = ()  # (x0, top, x1, bottom) per PDF word


@dataclass(frozen=True)
class FieldResult:
    path: str
    status: Status
    value: Any  # the claimed value when verified, else None
    claimed: Any
    reasons: tuple[str, ...] = ()
    span: Span | None = None
    text_method: str = "native"


def _locate(document: Document, ev: Evidence) -> tuple[str, Span] | str:
    if isinstance(document, PdfText):
        if ev.page is None or ev.cell is not None:
            return "location_invalid"
        if not 1 <= ev.page <= len(document.pages):
            return "location_not_found"
        page = document.pages[ev.page - 1]
        return page.text, Span(page.number, None, None, 0, len(page.text), page.text)
    if ev.cell is None or ev.page is not None:
        return "location_invalid"
    sheet, address = ev.sheet, ev.cell
    if sheet is None and "!" in address:  # "Sheet!A2" form
        sheet, address = address.rsplit("!", 1)
    if sheet is None:
        if len(document.sheets) != 1:
            return "location_invalid"
        sheet = document.sheets[0]
    cell = document.cell(sheet, address)
    if cell is None:
        return "location_not_found"
    return cell.value, Span(None, cell.sheet, cell.address, 0, len(cell.value), cell.value)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def find_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Source-text span of ``quote`` in ``text`` after normalisation; see module docstring for fuzzy rules."""
    qn = p.norm(quote)
    if not qn:
        return None
    tn, index = p.normalise(text)
    pos = tn.find(qn)
    if pos >= 0:
        return index[pos], index[pos + len(qn) - 1] + 1
    if len(qn) < FUZZY_MIN_LEN:
        return None
    starts = {
        max(0, b.a - b.b)
        for b in SequenceMatcher(None, tn, qn, autojunk=False).get_matching_blocks()
        if b.size >= 4
    }
    for anchor in (qn[:6], qn[-6:]):
        at = tn.find(anchor)
        while at >= 0:
            starts.add(at if anchor == qn[:6] else max(0, at + 6 - len(qn)))
            at = tn.find(anchor, at + 1)
    best: tuple[float, int, int] | None = None
    for s in starts:
        for length in range(len(qn) - 2, len(qn) + 3):
            window = tn[s : s + length].strip()
            if not window or _digits(window) != _digits(qn):
                continue
            ratio = SequenceMatcher(None, window, qn, autojunk=False).ratio()
            if ratio >= FUZZY_RATIO and (best is None or ratio > best[0]):
                best = (ratio, s, s + len(tn[s : s + length].rstrip()))
    if best is None:
        return None
    _, s, e = best
    return index[s], index[e - 1] + 1


def _canonical(spec: FieldSpec, value: Any) -> Any:
    """The comparable form of a claimed value; raises ValueError/TypeError when it is malformed."""
    match spec.kind:
        case Kind.money | Kind.rate | Kind.quantity:
            return p.parse_decimal(value)
        case Kind.date:
            return p.parse_iso_date(value)
        case Kind.identifier:
            return p.parse_identifier(value)
        case Kind.uom:
            return p.parse_uom(value)
        case Kind.name | Kind.text:
            if not isinstance(value, str) or not p.norm(value) or len(value) > 2000:
                raise ValueError("expected non-empty text")
            return p.norm(value)
        case Kind.enum:
            if (
                isinstance(value, bool) != any(isinstance(a, bool) for a in spec.allowed)
                or value not in spec.allowed
            ):
                raise ValueError(f"not one of {sorted(map(str, spec.allowed))}")
            return value
        case Kind.rounding:
            if not isinstance(value, Mapping) or set(value) != {"mode", "scale", "timing"}:
                raise ValueError("rounding needs mode, scale, timing")
            scale = value["scale"]
            if isinstance(scale, bool) or not isinstance(scale, int) or not 0 <= scale <= 6:
                raise ValueError("rounding.scale must be an integer 0-6")
            if (
                value["mode"] not in _ROUNDING_VALUES["mode"]
                or value["timing"] not in _ROUNDING_VALUES["timing"]
            ):
                raise ValueError("unknown rounding mode/timing")
            return dict(value)
        case Kind.period:
            if not isinstance(value, Mapping) or set(value) != {"kind", "start", "end"}:
                raise ValueError("measurement_period needs kind, start, end")
            return {
                "kind": value["kind"],
                "start": p.parse_iso_date(value["start"]),
                "end": p.parse_iso_date(value["end"]),
            }


canonical = _canonical  # public alias: the comparable form of a verified value (payload building)


def _check_value(spec: FieldSpec, value: Any, source: str) -> tuple[bool, tuple[str, ...]]:
    """(supported, reasons/flags) for an already-canonical ``value`` against the matched source text.

    Fail closed for every kind: interpretation fields (enums, rounding) are supported only when the keyword
    tables derive exactly the claimed reading; otherwise the reviewer sets them.
    """
    match spec.kind:
        case Kind.money | Kind.quantity:
            return value in p.numbers(source), ()
        case Kind.rate:
            return value in p.percents(source) or value in p.numbers(source), ()
        case Kind.date:
            hits = [amb for d, amb in p.dates(source) if d == value]
            return bool(hits), ("ambiguous_date",) if hits and all(hits) else ()
        case Kind.identifier:
            return p.contains_identifier(source, value), ()
        case Kind.uom:
            return value in p.uoms(source) or p.contains_identifier(source, value), ()
        case Kind.name | Kind.text:
            return p.contains_phrase(source, value), ()
        case Kind.enum:
            derived = p.keyword_values(spec.keywords or "", source)
            if derived == {value}:
                return True, ()
            if not derived:
                return False, ("value_not_derivable",)
            return False, ("value_contradicts_evidence" if value not in derived else "ambiguous_evidence",)
        case Kind.rounding:
            parts = p.rounding_parts(source)
            if any(parts[k] and value[k] not in parts[k] for k in parts):
                return False, ("value_contradicts_evidence",)
            if all(parts[k] == {value[k]} for k in parts):
                return True, ()
            return False, ("value_not_derivable",)
        case Kind.period:
            if any(
                pp["kind"] == value["kind"]
                and p.parse_iso_date(pp["start"]) == value["start"]
                and p.parse_iso_date(pp["end"]) == value["end"]
                for pp in p.measurement_periods(source)
            ):
                return True, ()
            stated = {d for d, _ in p.dates(source)}
            return value["start"] in stated and value["end"] in stated, ()
    raise AssertionError(spec.kind)  # pragma: no cover


_BLOCKING_INDICATORS = {
    IndicatorKind.hidden_text: "evidence_in_hidden_text",
    IndicatorKind.tiny_font: "evidence_in_hidden_text",
    IndicatorKind.off_page: "evidence_in_hidden_text",
    IndicatorKind.instruction: "evidence_in_instruction_text",
}


def verify_field(document: Document, cf: CandidateField, indicators: Sequence[Indicator] = ()) -> FieldResult:
    spec = spec_for(cf.path)

    def fail(*reasons: str, span: Span | None = None) -> FieldResult:
        return FieldResult(cf.path, Status.unsupported, None, cf.value, reasons=reasons, span=span)

    if cf.value is None:
        return FieldResult(cf.path, Status.absent, None, None)
    try:
        value = _canonical(spec, cf.value)
    except (ValueError, TypeError) as exc:
        return fail("value_invalid", str(exc)[:200])
    if cf.evidence is None or not cf.evidence.quote.strip():
        return fail("missing_evidence")
    located = _locate(document, cf.evidence)
    if isinstance(located, str):
        return fail(located)
    text, where = located
    found = find_quote(cf.evidence.quote, text)
    if found is None:
        return fail("quote_not_found")
    s, e = found
    source = text[s:e]
    boxes: tuple[tuple[float, float, float, float], ...] = ()
    text_method = "native"
    blocked: set[str] = set()
    if isinstance(document, PdfText) and where.page is not None:
        page = document.pages[where.page - 1]
        text_method = page.text_method
        hit = [w for w in page.words if w.start < e and w.end > s]
        boxes = tuple((w.x0, w.top, w.x1, w.bottom) for w in hit)
        blocked |= {_BLOCKING_INDICATORS[k] for w in hit for k in word_flags(page, w)}
    for ind in indicators:
        same_place = (ind.page, ind.sheet, ind.cell) == (where.page, where.sheet, where.cell)
        if same_place and ind.kind in _BLOCKING_INDICATORS and ind.start < e and ind.end > s:
            blocked.add(_BLOCKING_INDICATORS[ind.kind])
    span = Span(where.page, where.sheet, where.cell, s, e, source, boxes)
    if blocked:
        return fail(*sorted(blocked), span=span)
    supported, flags = _check_value(spec, value, source)
    if not supported:
        return fail(*(flags or ("value_not_in_evidence",)), span=span)
    return FieldResult(cf.path, Status.verified, cf.value, cf.value, flags, span, text_method)


def verify_candidate(
    document: Document, candidate: Mapping[str, CandidateField], indicators: Sequence[Indicator] | None = None
) -> dict[str, FieldResult]:
    inds = detect(document) if indicators is None else indicators
    return {path: verify_field(document, cf, inds) for path, cf in candidate.items()}


# ---- pass agreement and derived confidence ------------------------------------------------------------


class Confidence(IntEnum):
    """Ordered for ``min``; serialise with ``.label``."""

    unsupported = 0
    conflict = 1
    low = 2
    medium = 3
    high = 4

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class MergedField:
    key: str  # rows keyed by content, e.g. ``items[BRK-3P-100A].deviated_cost``
    critical: bool
    value: Any  # the draft value (None for conflict/unsupported/absent)
    confidence: Confidence
    reasons: tuple[str, ...]
    a: FieldResult | None
    b: FieldResult | None


def _row_keys(results: Mapping[str, FieldResult]) -> dict[str, str]:
    """``items[3]`` -> ``items[BRK-3P-60A]`` etc., so that rows of the two passes align by content."""
    key_attr = {
        "items": ("part_number", Kind.identifier),
        "customers": ("name", Kind.name),
        "tiers": ("lower_bound", Kind.money),
    }
    out: dict[str, str] = {}
    for path, r in results.items():
        m = re.fullmatch(r"(items|customers|tiers)\[(\d+)\]\.(\w+)", path)
        if not m or m.group(3) != key_attr[m.group(1)][0]:
            continue
        try:
            key = str(_canonical(FieldSpec(key_attr[m.group(1)][1], False), r.claimed))
        except ValueError, TypeError:
            continue
        out[f"{m.group(1)}[{m.group(2)}]"] = f"{m.group(1)}[{key}]"
    return out


def _keyed(results: Mapping[str, FieldResult]) -> dict[str, FieldResult]:
    rows = _row_keys(results)
    out: dict[str, FieldResult] = {}
    for path, r in results.items():
        m = re.match(r"(\w+\[\d+\])(.*)", path)
        key = (rows.get(m.group(1), m.group(1).replace("[", "[#")) + m.group(2)) if m else path
        out[key] = r
    return out


def _effective(r: FieldResult | None) -> Any:
    if r is None or r.status is not Status.verified:
        return None
    return _canonical(spec_for(r.path), r.value)


def _merge(key: str, a: FieldResult | None, b: FieldResult | None, warned: bool) -> MergedField:
    spec = spec_for((a or b).path)  # type: ignore[union-attr]
    ea, eb = _effective(a), _effective(b)
    reasons: list[str] = []
    for r in (a, b):
        if r is not None:
            reasons += [x for x in r.reasons if x not in reasons]
    if ea is None and eb is None:
        # Both passes say "not stated": null is a legitimate value for many fields (no cap, open end,
        # base UoM), so it is flagged for acknowledgement; whether it is *required* is the validator's call.
        if any(r is not None and r.status is Status.unsupported for r in (a, b)):
            return MergedField(key, spec.critical, None, Confidence.unsupported, tuple(reasons), a, b)
        return MergedField(key, spec.critical, None, Confidence.low, (*reasons, "not_stated"), a, b)
    if ea != eb:
        reasons.append("passes_disagree")
        if spec.critical or (ea is not None and eb is not None):
            return MergedField(key, spec.critical, None, Confidence.conflict, tuple(reasons), a, b)
        chosen = a.value if a is not None and ea is not None else b.value if b is not None else None
        return MergedField(key, spec.critical, chosen, Confidence.low, tuple(reasons), a, b)
    if a is None or b is None:  # equal effective values imply both passes verified the field
        raise RuntimeError(f"{key}: inconsistent pass results")
    conf = Confidence.high
    if "ocr" in (a.text_method, b.text_method):
        conf = Confidence.medium
        reasons.append("ocr_text")
    if "ambiguous_date" in reasons:
        conf = Confidence.low
    if warned:
        conf = Confidence(max(Confidence.low, conf - 1))
        reasons.append("validator_warning")
    return MergedField(key, spec.critical, a.value, conf, tuple(reasons), a, b)


def compare_passes(
    a: Mapping[str, FieldResult], b: Mapping[str, FieldResult], warned_keys: frozenset[str] = frozenset()
) -> dict[str, MergedField]:
    ka, kb = _keyed(a), _keyed(b)
    return {k: _merge(k, ka.get(k), kb.get(k), k in warned_keys) for k in dict.fromkeys([*ka, *kb])}


@dataclass(frozen=True)
class VerificationReport:
    fields: dict[str, MergedField]
    indicators: tuple[Indicator, ...]
    agreement_type: str | None
    draft_confidence: Confidence = field(init=False)
    blocking: tuple[str, ...] = field(init=False)  # critical conflict/unsupported: block "submit"
    needs_acknowledgement: tuple[str, ...] = field(init=False)  # everything below high
    needs_manual_entry: bool = field(init=False)  # verification left no critical value

    def __post_init__(self) -> None:
        critical = [f for f in self.fields.values() if f.critical]
        set_ = object.__setattr__
        set_(self, "draft_confidence", min((f.confidence for f in critical), default=Confidence.unsupported))
        set_(self, "blocking", tuple(f.key for f in critical if f.confidence <= Confidence.conflict))
        set_(
            self,
            "needs_acknowledgement",
            tuple(f.key for f in self.fields.values() if f.confidence < Confidence.high),
        )
        set_(self, "needs_manual_entry", not any(f.value is not None for f in critical))


SchemaValidator = Callable[[Mapping[str, Any]], Sequence[str]]


def run_verification(
    document: Document,
    pass_a: Mapping[str, Any],
    pass_b: Mapping[str, Any],
    *,
    schema_validator: SchemaValidator | None = None,
    warned_keys: frozenset[str] = frozenset(),
) -> VerificationReport:
    """Both passes' §5 JSON -> report. Raises :class:`CandidateFormatError` when a pass is malformed.

    ``schema_validator`` is the hook for S4's payload schema: it returns error strings for a raw pass
    (non-empty = the pass is rejected). ``warned_keys`` are merged keys the AgreementValidator warned about.
    """
    for raw in (pass_a, pass_b):
        if schema_validator is not None and (errors := tuple(schema_validator(raw))):
            raise CandidateFormatError("; ".join(errors))
    indicators = detect(document)
    fields = compare_passes(
        verify_candidate(document, flatten(pass_a), indicators),
        verify_candidate(document, flatten(pass_b), indicators),
        warned_keys,
    )
    type_field = fields.get("header.agreement_type")
    agreement_type = type_field.value if type_field is not None else None
    if agreement_type is not None:  # rules of the other agreement type that both passes left null
        fields = {
            k: f
            for k, f in fields.items()
            if agreement_type in spec_for((f.a or f.b).path).applies_to  # type: ignore[union-attr]
            or (f.a is not None and f.a.status is not Status.absent)
            or (f.b is not None and f.b.status is not Status.absent)
        }
    return VerificationReport(fields, tuple(indicators), agreement_type)


__all__ = [
    "SPECS",
    "CandidateField",
    "CandidateFormatError",
    "Confidence",
    "Evidence",
    "FieldResult",
    "FieldSpec",
    "Kind",
    "MergedField",
    "Span",
    "Status",
    "VerificationReport",
    "canonical",
    "compare_passes",
    "find_quote",
    "flatten",
    "run_verification",
    "spec_for",
    "verify_candidate",
    "verify_field",
]
