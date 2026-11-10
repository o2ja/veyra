# C2 — S6 AI extraction backend (no live LLM)

Agent id: `c2-s6-backend`. Branch: `s6-backend`. Test DB: `rebate_test_c2`. Do NOT run the negative control
(C1 owns `rebate_negctl` this wave); run the isolation suite on your own test DB.

**OD-2 is UNRESOLVED: no provider call, no network, no API key.** The adapter is real code against the
provider SDK (`anthropic`, pinned) but every test drives it through recorded-response fixtures (a transport-level
replay of real-shaped responses you author from the synthetic documents and ground truth); the live path is
only reachable under `-m live_llm` with a key, which is deselected and never run.

## Read
`COMMON.md`; `CLAUDE.md` rules section; `AI_PROCESSING.md` (entire); `SECURITY.md` §4–§5; ADR-011/012;
`IMPLEMENTATION_PLAN.md` §S6; `DATABASE_SCHEMA.md` §2 (roles) and the extraction tables; `TEST_DATA_SPEC.md` §2.2;
`docs/reports/notes/a3-s6-pure.md` (what exists, hook points, open items).
Reuse: `modules/extraction/{documents,parsers,injection,verification}.py` (on main), `modules/agreements`
(drafts, validator, payload schema), `modules/files`, `modules/ingestion` (suggest-mapping endpoint), `core/jobs.py`.

## Build
- S6.1 OCR execution: `ocrmypdf`/Tesseract are in the worker image (orchestrator added them). Run OCR only for
  `needs_ocr` pages, in a subprocess with CPU/memory/time limits and a per-job temp dir; `text_method="ocr"`.
- S6.2 gateway `modules/extraction/gateway.py`: provider interface + Anthropic adapter, versioned prompt
  templates, structured output, two passes, one repair attempt, retries/backoff (tenacity), cost tracking + per-
  tenant caps, per-tenant opt-in check (AI disabled → no call, clear state), redacted logging. Model id from
  config (default per CLAUDE/TECH_STACK; if unspecified use `claude-sonnet-5-5`).
- S6.4 DB role `rebate_ai_writer` grants per DATABASE_SCHEMA §2; migrations `document_extractions`,
  `ai_invocations`; gateway sessions use `DATABASE_URL_AI`.
- S6.5 job `extract_agreement` → `agreement_drafts(source=ai_extraction)` via the shared draft pipeline and
  validator (plug the S4 validator into `run_verification(schema_validator=...)`); failure states incl.
  `needs_manual_entry`. Add `calculation_basis`, `tier_mode`, `volume_basis` to the critical fields in
  `verification.SPECS` (owner-approved) and update AI_PROCESSING.md §4.5.
- S6.6 `suggest_mapping` (headers + ≤ 5 sample rows) replacing S3's `ai_not_enabled` response when the tenant
  opted in; still `ai_not_enabled` otherwise.
- S6.8 evaluation harness (`-m live_llm`, deselected) + recorded fixtures for CI; record fixture-based baseline
  metrics in notes and mark "live baseline pending OD-2".
- APIs the frontend needs: extraction status/result with evidence (page, bbox), AI usage per tenant, AI opt-in
  setting. Permissions, audit, isolation suite for every new table/route.

## Tests (S6.9)
Golden docs vs `expected_extraction/*` through the full job with recorded responses; injection, missing-info,
multipage, Excel, OCR page (built PDF); failure injection (timeouts, malformed output → repair → fail,
cost cap hit, provider error); `rebate_ai_writer` cannot write any other table (real role, real DB);
AI-disabled workspace: imports, agreements (manual), runs all work and no gateway call happens; no AI output
affects calculations before approval.

## You own
`backend/src/rebate/modules/extraction/**` (new files + the SPECS line), new migration files, worker job
registration, `backend/tests/extraction/**` and integration/isolation additions, `docs/AI_PROCESSING.md` §4.5,
`docs/reports/notes/c2-s6-backend.md`.
