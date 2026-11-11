# C2 (S6 AI extraction backend) - deviations, decisions, doc deltas

Branch `s6-backend`, migration `20261113_0900_extraction` (down_revision `20261112_0900_agreements`).
**OD-2 is unresolved**: no provider call, no network, no key, no real document anywhere in this work. The
Anthropic adapter is real code (SDK `anthropic==2.7.0`, already pinned); every test drives it through
recorded-response fixtures at the HTTP transport (`tests/extraction/replay.py`). The live path exists only under
`-m live_llm` (deselected, needs `ANTHROPIC_API_KEY`, never run).

## What was built

- `extraction/ocr.py` (S6.1): `ocrmypdf` for `needs_ocr` pages only, subprocess with RLIMIT_AS/CPU/FSIZE, scrubbed
  environment, private temp dir, wall-clock timeout; page replaced with `text_method="ocr"`.
- `extraction/gateway.py` (S6.2): `Provider` protocol + `AnthropicProvider` (one forced tool call, no other tools),
  `Gateway.call`: opt-in check, per-tenant daily cost/token caps (UTC day), tenacity retries (4 attempts, exponential
  backoff + jitter, `Retry-After` honoured), one repair attempt, ledger row per call under `rebate_ai_writer`, logs
  with ids/hashes/counts only. `prompts.py`: versioned templates (`agreement-v1`, `mapping-v1`), tool JSON schemas.
- `extraction/payload.py`, `service.py`, `jobs.py`, `router.py`, `mapping.py`, `models.py`.
  Job `extract_agreement`: text/OCR -> two passes -> `run_verification` (schema hook = `flatten` format check) ->
  payload -> `agreements.create_ai_draft` (shared validator) -> validator warnings feed back into confidence ->
  result written by the AI role. States: `needs_manual_entry` (ai_disabled, corrupt/encrypted/too large/no text,
  too_long_for_model, model refusal, nothing verified), `failed` (provider_unavailable after 3 job attempts x 4 calls,
  invalid_output, daily_cap_reached, provider_not_configured). No AI failure touches imports or manual agreements.
- API: `POST /document-extractions` (202 queued / 200 reuse; `rerun`), `GET /document-extractions[/{id}]`
  (per-field value, confidence, reasons, evidence page/quote/word boxes, both passes, injection indicators,
  validation, `draft_id`), `GET /ai/usage` (today vs caps + per-day totals), `POST /imports/{id}/suggest-mapping`
  (moved from ingestion). Opt-in is the existing `PATCH /settings {ai_processing_enabled}` (tenancy, audited).
- Migration: `document_extractions`, `ai_invocations` (+ FK `agreement_drafts.extraction_id`), RLS ENABLE+FORCE via
  helper, final-state/identity trigger, append-only ledger, reviewed grants (below).
- `verification.SPECS`: `calculation_basis`, `tier_mode`, `volume_basis` now critical (owner-approved); AI_PROCESSING.md
  section 4.5 updated. `verification.canonical` exported (public alias).

## Decisions (CLAUDE.md 18.1)

1. **Grants stricter than DATABASE_SCHEMA.md section 2.** `rebate_ai_writer`: SELECT `file_objects`, `document_extractions`,
   `ai_invocations`; UPDATE only the 12 result columns of `document_extractions`; INSERT `ai_invocations`. It has **no**
   grant on `agreement_drafts` / `agreement_draft_revisions` (section 2 allows them): the draft is created by the worker's
   `rebate_app` session because the shared AgreementValidator needs master-data reads the AI role must not have. The app
   role may only INSERT the *queued* request row (column-level grant: cannot set results) and SELECT; it cannot write
   results or the ledger. Section 2 / 3.6 should say so.
2. **Draft creation entry point**: `agreements.create_ai_draft` (public, in S4 `service.py`, exported). `POST
   /agreement-drafts` keeps refusing `source=ai_extraction` (409 `ai_not_enabled`); API_SPECIFICATION.md line 149/166 say
   it should return a 202 job. I used `POST /document-extractions` because `agreements` cannot import `extraction`
   (import-linter acyclic). The 409 message in `agreements.service.AiNotEnabled` ("arrives with a later stage") is now stale.
3. **Percentages are converted by the backend** (`payload.normalise_rates`), the prompt asks for money/quantities as plain
   digit strings and rates as printed (`2.5%`); matches AI_PROCESSING.md section 5.
4. **Payload building**: only verified, pass-agreeing values enter the payload; conflicts/unsupported/not stated are left
   out so the validator reports them "required". Master-data names resolve by normalised name (case/space/punctuation and
   the `(SYNTHETIC)` label ignored) to codes; an unresolved customer/manufacturer keeps its printed name as the code (visible
   as unresolved, never guessed). `header.name` is derived (number + manufacturer). Structural defaults no contract states
   (price_check flag/0.005, cap_scope per_item, product_scope, tier_metric amount, returns reduce_volume) are the `testdata`
   manual-payload defaults; the reviewer sees them as ordinary values.
5. **Job-level retry**: provider outage -> job retried with backoff (max 3 attempts); finished passes are stored
   (`pass_results`) and never paid twice. Crash window (AI-role write committed, job commit lost) is repaired on retry by rebuilding the
   draft from the stored result.
6. **Mapping suggestion is synchronous (200)**, not a 202 job as in API_SPECIFICATION.md line 135: one short call, headers +
   at most 5 staged rows, cells cut to 80 chars, card numbers masked, answer limited to listed headers. 503 `ai_unavailable`
   on any gateway failure. Order: foreign id -> 404, then opt-in -> 409.
7. **Caps/prices**: defaults are ASSUMPTIONS (config): $3 / $15 per million tokens, $25 and 2M tokens per tenant per UTC day,
   120 s request timeout, 16k output tokens, model `claude-sonnet-5-5` (pass B model optional `AI_MODEL_PASS_B`).
8. Encrypted PDFs never reach the job: ClamAV heuristics (`Heuristics.Encrypted.PDF`) quarantine them at upload
   (`scan_status=infected`), so `POST /document-extractions` answers 409. The job's `encrypted` branch (owner-password-only
   files that pass the scan) is covered by the pure unit tests only.

## Edits outside the owned files (necessary; please review)

`core/config.py` (AI settings), `api/app.py` + `workers/__main__.py` (router/job registration), `migrations/env.py` (model
import), `modules/audit/events.py` (3 actions, `document_extraction` audited fields, `extraction_id` on drafts),
`modules/agreements/{service,models,__init__}.py` (`create_ai_draft`, FK mirror, export), `modules/ingestion/{router,service,
__init__}.py` (stub route removed, `mapping_sample`), `tests/conftest.py` (`live_llm` marker, deselected like `performance`),
`tests/isolation/{conftest,test_db_rls,test_routes}.py` (S6 tables/routes), `Makefile` (`API_RUN` passes
`REBATE_AI_WRITER_PASSWORD`; the tests build the AI-role URL from it), `.github/workflows/ci.yml` (installs
`ocrmypdf tesseract-ocr-eng` before pytest so the OCR test runs; the worker image already has them).

## Deployment deltas for the orchestrator

- worker (and api for `suggest-mapping`): `DATABASE_URL_AI=postgresql+psycopg://rebate_ai_writer:...@postgres:5432/rebate`
  and, only after OD-2, `ANTHROPIC_API_KEY`; `compose.dev.yml` is unchanged (shared). Without `DATABASE_URL_AI` the
  extraction job dead-letters visibly and the request stays `queued`.
- DEPLOYMENT.md / SECURITY.md: the OCR subprocess uses rlimits only (no seccomp/network namespace); a container-level
  sandbox is still the recommended hardening.

## Doc deltas proposed (not applied; outside owned docs)

- API_SPECIFICATION.md: add `/document-extractions` (POST/GET), `/ai/usage`; `suggest-mapping` is 200 sync; line 166.
- DATABASE_SCHEMA.md section 2 (grants as in decision 1) and section 3.6 (`document_extractions` gets nothing beyond the
  documented columns plus FK/trigger; `ai_invocations` purposes/status CHECKs).
- AI_PROCESSING.md section 4.4/6 (A3 proposals): fail closed for interpretation fields; null in both passes = low + `not_stated`.
  Section 3.5 "chunk": **not implemented** - documents over 400k characters go to manual entry (`too_long_for_model`).
- PRODUCT/FR: per-field acknowledgement of fields below `high` is **not enforced server-side** (S4 submit only checks
  validator warnings); critical conflict/unsupported fields do block submit because their values are absent and the
  validator reports them required. The checkbox UX (S6.7) is frontend.

## Fixture baseline metrics (recorded responses: live baseline pending OD-2)

Deterministic layer (verification, parsing, validation, draft building) fed with answers a correct model would give
(values from `expected_extraction/*`, quotes hand-copied; pass B states rates as percentages). Source:
`backend/tests/extraction/fixtures/baseline_fixture_metrics.json`, regression-tested by `test_baseline.py`.

| Metric | Value |
|---|---|
| Documents completed | 7 / 7 |
| Hallucinated values in drafts (contradict ground truth) | 0 |
| Precision (draft values equal to ground truth) | 1.0 |
| Recall (ground-truth values present in draft) | 0.9237 |
| Fully correct documents | 3 / 7 (SPA-884, COPR-220, injection test) |
| Evidence validity (pass A claimed fields verified) | 0.9414 |
| Critical-field conflict rate | 0.0 |

Recall below 1 is by design (fail closed): `rules.rounding` where the text states only mode and scale (REB-VOLT, REB-NWND),
`spa_lines` not stated (REB-VOLT), customer scope not derivable ("We will support ..."), abbreviated customer names that
do not resolve, cost_source/spa_reference not stated in the Brightline sheet. These are reviewer decisions, not misses.
The numbers say nothing about a real model; the quality bar with the owner is still to be agreed after the first live run.

## Not done / limitations

- Live run and live baseline (OD-2). No concurrency limit on extractions and no extra rate limit on the new endpoints beyond
  the global ones. No chunking of long documents. Remittance PDF extraction (S8) not started. Frontend (S6.7) not part of this task.
- Negative control not run (C1 owns `rebate_negctl`); the new isolation tests use `breach()` messages so it can detect them.
