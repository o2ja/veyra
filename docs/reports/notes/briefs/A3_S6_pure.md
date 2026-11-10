# A3 — S6 pure parts: text/table extraction + deterministic verification

Agent id: `a3-s6-pure`. Branch: `s6-pure`. Test DB: `rebate_test_a3` (you should not need a DB).
OD-2 (LLM provider/data terms) is UNRESOLVED: no LLM calls of any kind, no network. Nothing you build calls a
provider.

## Read
`CLAUDE.md`; `docs/AI_PROCESSING.md` (ENTIRE); `SECURITY.md` §4–§5; ADR-011/012 in `ARCHITECTURE_DECISIONS.md`;
`TEST_DATA_SPEC.md` §2.2; `IMPLEMENTATION_PLAN.md` §S6; `BUSINESS_LOGIC.md` §3 (agreement fields) for value types;
`testdata/generated/agreements/*` (PDFs, XLSX) and `ground_truth/expected_extraction/*.json`.

## Build in `backend/src/rebate/modules/extraction/` (pure files only: no models, no migrations, no router, no
imports from other `rebate.modules.*`; `core` utilities are fine)
- S6.1 (minus OCR): text/table extraction with `pdfplumber` (page text with page numbers and char/word positions
  needed for evidence highlighting), caps (pages, chars, file size, time), encrypted/corrupt PDF handling as
  explicit failure outcomes, Excel serialisation with cell addresses (openpyxl read-only, values only, no
  formulas evaluated). OCR fallback (Tesseract/ocrmypdf) is NOT available in the image: implement the decision
  point ("needs OCR" outcome when a page has no text layer) and leave OCR execution to the S6 backend agent —
  state this in your report; do not fake OCR output.
- S6.3 deterministic verification: evidence verifier (quoted evidence must exist in the cited page/cell after
  documented normalisation; fail closed), value parsers (money, percent, dates, UoM, SKU, quantities — Decimal,
  no floats), pass-agreement between the two passes, derived confidence (per AI_PROCESSING.md, never the model's
  self-reported confidence), injection indicators. Define the input type as "a candidate extraction" (field →
  value, evidence {page/cell, quote}) per AI_PROCESSING.md. Schema validation against the agreement payload
  schema waits for S4 (later agent) — expose a hook point and say so.

## Tests in `backend/tests/extraction/`
Unit tests for every parser and verifier rule; golden docs: run your text extraction on every synthetic PDF/XLSX
and check that every evidence quote/value in `expected_extraction/*.json` verifies against the extracted text
(true positives), and that tampered candidates (wrong page, altered digits, quote not in doc, injected
instructions from `SYNTHETIC_spa_injection_test_*`, missing-info doc) are rejected/flagged; multipage; Excel;
corrupt and encrypted PDFs built in the test; caps. Expected values come from the ground-truth JSON or the doc,
never from your code's output. Run in the container (COMMON.md); finish with lint gates.

## You own
`backend/src/rebate/modules/extraction/**` (pure files listed above + `__init__.py`), `backend/tests/extraction/**`,
the `extraction-internals` protected contract block in `backend/importlinter.ini` (add only that block, same shape
as `files-internals`; the boundaries meta-test requires it), `docs/reports/notes/a3-s6-pure.md`.
