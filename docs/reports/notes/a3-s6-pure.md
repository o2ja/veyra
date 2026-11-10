# A3 (S6 pure parts) — deviations, decisions, doc deltas

Scope: S6.1 without OCR execution, S6.3. Code: `backend/src/rebate/modules/extraction/{documents,parsers,injection,verification}.py`.
No models, migrations, router, provider calls or network. OD-2 untouched.

## Decisions (CLAUDE.md §18.1, for the S6 report / an ADR if the orchestrator wants one)

1. **Page text is rebuilt from `pdfplumber` words** (lines grouped by `top` within 3 pt) so every word keeps its
   char offsets, bbox, font size and fill colour. Evidence spans return source offsets plus word boxes.
2. **Fail closed for every field kind.** AI_PROCESSING.md §4.4 ("failing fields ... unsupported") and §6 ("value
   parsed deterministically ... otherwise flagged") read differently for interpretation fields. Chosen: enums and
   rounding are supported only if the keyword tables (`parsers.KEYWORDS`, `rounding_parts`) derive exactly the
   claimed reading; otherwise `unsupported` (`value_not_derivable` / `value_contradicts_evidence` /
   `ambiguous_evidence`). Consequence: unusual wording means the reviewer sets the field; tables grow with the
   golden set. Example: REB-VOLT "rounded to the nearest cent" states no per-line timing → rounding unsupported.
3. **Fuzzy matching** (quotes ≥ 20 chars, ratio ≥ 0.95, `difflib`) requires an identical digit sequence; the value
   is parsed from the matched *source* text, never from the model's quote.
4. **Evidence inside flagged text is rejected**: white fill (RGB/gray ≥ 0.95, CMYK no ink), font < 5 pt, off-page,
   or a sentence matching the instruction patterns → `evidence_in_hidden_text` / `evidence_in_instruction_text`.
5. **Null in both passes** → value null, confidence `low`, reason `not_stated` (acknowledgement required, not
   blocking): null is legitimate for many fields (no cap, base UoM, open end). Whether a field is *required* is the
   AgreementValidator's call (S4), which blocks via its errors. Rules of the other agreement type that both passes
   left null are dropped (e.g. `tier_mode` on an SPA).
6. **Pass agreement**: rows aligned by content (items by part number, customers by normalised name, tiers by lower
   bound). Critical disagreement → `conflict`; non-critical: both values present and different → `conflict`, one
   null → the verified value with `low`.
7. **Confidence**: high when both passes verify the same value; OCR page → medium; ambiguous numeric date → low;
   validator warning → one step down (floor low). Draft confidence = min over critical fields;
   `needs_manual_entry` when no critical field keeps a value.
8. **Numeric dates are US MM/DD/YYYY** (ASSUMPTION, OD-1 US vertical); both parts ≤ 12 and different → ambiguous.
   "nearest cent" is read as half-up (BUSINESS_LOGIC.md §1.2 default).
9. **Caps (defaults in `documents.Limits`)**: 60 pages, PDF 25 MB, XLSX 50 MB (SECURITY.md §4); 500k chars, 60 s,
   200k cells, 200 MB uncompressed / ratio 100 for XLSX zip-bomb guard (my defaults). The time cap is checked
   between pages/rows with `time.monotonic` (a resource guard, not business time); it cannot interrupt one
   pathological page — the worker-level time/CPU limit (SECURITY.md §4 "parsing sandbox") still applies.
10. Owner-password-only encrypted PDFs (open with an empty user password) are still `encrypted` →
    `needs_manual_entry`, per §3.3.

## For the S6 backend agent (not done here)

- **OCR is not executed**: pages with < 30 chars and an image get `Page.needs_ocr = True`
  (`PdfText.needs_ocr_pages`). ocrmypdf/Tesseract are not in the image; the S6 job must run OCR in a restricted
  container and replace the page with `text_method="ocr"` (the verifier then lowers confidence to medium).
- **Schema validation hook**: `run_verification(..., schema_validator=...)` takes a callable returning error
  strings for a raw pass (non-empty → `CandidateFormatError`, i.e. the repair attempt). Plug in S4's agreement
  payload schema / `AgreementValidator`; pass its warning keys as `warned_keys`.
- Chunking/redaction for prompts (§3.5 preprocess) and the prompt itself are S6.2 (gateway); `SheetText.serialise()`
  gives the `sheet!address<TAB>value` form.
- Mapping extracted names to codes (`manufacturer_name` → `manufacturer_code`, customer names → codes) is masterdata.

## Doc deltas proposed

- AI_PROCESSING.md §4.4/§6: state decision 2 (fail closed for interpretation fields) and decision 5 (null → low).
- AI_PROCESSING.md §4.5 critical list does not name `calculation_basis`, `cost_source`, `tier_mode`,
  `volume_basis`, `spa_reference_required`, `part_number`, `external_number`, `manufacturer_name`; they are coded
  non-critical (`verification.SPECS`) to follow the doc, but they determine amounts or identity. Recommend adding
  at least `calculation_basis`, `tier_mode`, `volume_basis` to the critical list (one-line change in `SPECS`).
- AI_PROCESSING.md §5: the schema has item `uom` but the payload uses `claim_uom`; kept `items[].uom` as in §5.
- TEST_DATA_SPEC.md §2.2 gaps unchanged: no scanned PDF in the golden set, so the OCR decision is tested with a
  built PDF; encrypted/corrupt/over-cap PDFs are built in the tests.

## Limitation (known)

- Value-in-quote cannot detect a column swap inside one table row (e.g. reference vs deviated cost when the quote is
  the whole row); pass agreement and the reviewer catch it. A table-cell-aware check is possible later.
