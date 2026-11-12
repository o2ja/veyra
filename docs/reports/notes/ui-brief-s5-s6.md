# UI brief — S5 calculation + S6 AI extraction frontends (self-contained)

**Goal.** Add the calculation-run screens (S5.6) and the AI-extraction review screens (S6.7) to Veyra Recover
(rebate/claim recovery SaaS; React 18 + TS + Vite + React Router + TanStack Query + Tailwind; generated OpenAPI
client). Everything talks to the real API. Amounts are computed only by the server: the UI displays them.

## Pattern to copy (read these, nothing else in the repo)
Feature folder `frontend/src/features/agreements/` (merged S4 UI):
- list page `agreements-page.tsx`, detail `agreement-detail-page.tsx`, editor `draft-editor-page.tsx`
  (validation panel, `If-Match`/412 reload, `Idempotency-Key`, read-only states), helpers `common.tsx`, pure logic
  `logic.ts` + unit test `logic.test.ts`
- shared: `frontend/src/components/ui.tsx` (`Button, Alert, ErrorAlert, Card, Loading, Empty, PermissionDenied,
  Field, fieldError`), `frontend/src/features/masterdata/common.tsx` (`usePager, PagerControls, DataTable,
  SelectField, ManufacturerSelect`), `frontend/src/lib/api/client.ts` + `errors.ts` (`api`, `unwrap`, `ApiError`),
  `frontend/src/lib/session.tsx` (`useSession().can(perm)`)
- routing `frontend/src/app/routes.tsx` (`guarded(path, perm, el)`), nav `frontend/src/app/app-layout.tsx`
- e2e `frontend/e2e/agreements.spec.ts` + `frontend/e2e/support.ts` (register, MFA, invite, axe helper)
- types: `frontend/src/api/schema.d.ts` → `components["schemas"]["<Name>"]` (names below; search by name)

## Folders
- **May edit:** `frontend/src/features/calculations/**` and `frontend/src/features/extractions/**` (new),
  `routes.tsx`/`app-layout.tsx` (route + nav lines), the preview panel hook-in inside
  `features/agreements/draft-editor-page.tsx` (one section, no other changes there),,
  `frontend/e2e/calculations*.spec.ts`, `frontend/e2e/extractions*.spec.ts`, `docs/reports/notes/ui-report-s5-s6.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `docs/` (except your report),
  `frontend/src/api/` (generated), `package.json`/lockfile (no new dependencies), other feature folders.
  `features/imports/**` and `features/masterdata/**` belong to another agent working in parallel (read-only for you).
- Branch `ui/s5-s6`, worktree `D:eyraeyra-ui-s5s6`, based on main `e152d78` (client in sync with the API;
  CI `api-contract` green). Another agent will regenerate `frontend/src/api/` on main (import row errors, reason
  codes: no S5/S6 types change). Never regenerate it yourself; the orchestrator merges main into your branch if needed.
- e2e: `e2e/support.ts` gives every test its own client IP (per-IP register limit); keep importing it.

## Rules (CLAUDE.md §10 + project rules)
1. Generated client and types only; never hand-write API types. Untyped `trace`/`totals`/`scope` objects are
   rendered generically (labelled key/value tree), never re-typed or re-computed.
2. **No business calculations in the UI**: no sums, no amount × rate, no tier progress maths, no deadline
   arithmetic, no confidence logic. Show the server's decimal strings exactly (format with grouping only).
   `amount_to_next_tier`, totals, deltas: display as given.
3. Server is the authority on permissions; UI hides/disables only. Handle 403 everywhere.
4. No secrets/tokens/sensitive data in code, `localStorage`, `console`. Document text and `notes` from the AI are
   untrusted: render as plain text (never HTML), labelled "AI note".
5. Every screen handles loading, empty, error, permission-denied; keyboard accessible, labelled, axe-clean
   (WCAG 2.1 AA). No mock data, fake responses, placeholder screens or TODO stubs.

## API conventions
Prefix `/api/v1`. Errors via `ApiError.code` (`validation_error` 422 + `details[]`, `invalid_state` 409,
`precondition_failed` 412, `rate_limited`/`too_many_extractions` 429, `ai_not_enabled` 409, `ai_unavailable` 503,
`split_period_terms_need_decision` 422 — show its message verbatim). Lists: `{data, page:{has_more,
next_cursor}}`, `limit` ≤ 200 + `cursor`. `Idempotency-Key`: `crypto.randomUUID()` per user action, reused on
retry of the same click. Permissions: reads `data.read`; start runs + draft preview `calculation.run`; start
extraction `agreement.draft`; AI mapping `ai.use`; AI opt-in is the existing admin setting.
Run status: `queued, running, completed, failed, cancelled`. Result status: `eligible, exception, netted,
adjustment, not_applicable`.

## Screens — S5 calculations

### 1. Runs — `/calculations` (data.read)
`GET /calculation-runs?status&limit&cursor` → `RunList` (`RunOut`: id, status, run_date, as_of, data_cutoff,
scope, totals, engine_version, created_at, started_at, completed_at, error). "New run" (calculation.run) form:
`RunIn{date_from, date_to, manufacturer_ids[], as_of?}` → `POST /calculation-runs` with `idempotency-key` →
202 `RunAccepted{run, job}`; then poll `GET /calculation-runs/{id}` every 2 s while queued/running.
Accept: failed run shows `error` (code/message); 422 field errors; status filter in URL.

### 2. Run detail + results grid — `/calculations/:runId` (data.read)
`RunOut` header (totals as key/value), `GET /calculation-runs/{id}/results?status&exception_code&manufacturer_id
&agreement_id&limit&cursor` → `ResultList` (`ResultOut`: document_number, line_number, invoice_date, status,
exception_code, claim_amount, claim_unit_amount, eligible_quantity, excess_quantity, flags[]). Filters in URL,
keyset pager, row → screen 3. "Propose adjustments" (calculation.run) → `POST
/calculation-runs/{id}/propose-adjustments` → `ProposalsOut{compared, proposed[AdjustmentOut]}` shown as a table.
Accept: exception filter works; 0 results → empty state; money right-aligned, server strings unchanged.

### 3. Explain this number — `/calculations/results/:resultId` (data.read)
`GET /calculation-results/{id}` → `ResultDetail` (+ `trace`, `trace_ref`, `inputs_hash`). Render `trace` as an
expandable, labelled tree (keyboard: arrow keys or buttons with aria-expanded). Link to the source line
(`/lines/sales/:sales_document_line_id`, existing route) and the agreement version
(`/agreements/versions/:agreement_version_id`).

### 4. Rebate accruals and tracker — `/calculations/rebates` (data.read)
`GET /rebate-tracker` → `TrackerOut{data[TrackerRow]}` (agreement, period, cumulative_qualifying,
cumulative_payout_base, cumulative_earned, accrued_to_date, tier_number, tier_rate, next_tier_lower_bound,
amount_to_next_tier, claim_deadline, tiers[]). Tier table per row with the current tier highlighted (by
`tier_number` equality, not computed). Ledger: `GET /rebate-accruals?agreement_id&run_id&limit&cursor` →
`AccrualList` (delta, prior_cumulative_accrued, flags[]).

### 5. Draft preview panel — inside the agreement draft editor (calculation.run)
`POST /agreement-drafts/{id}/preview-calculation` `DraftPreviewIn{limit}` → `DraftPreviewOut{kind, run_date,
sample_size, persisted:false, totals, lines[DraftPreviewLine], accrual}`. Label clearly "Preview — not saved".
Accept: 422 messages shown (e.g. unsupported rules); nothing else in the editor changes.

## Screens — S6 AI extraction

### 6. Extractions — `/extractions` (data.read; start: agreement.draft)
`GET /document-extractions?file_id&limit` → `ExtractionList{data[ExtractionOut]}`. Start: pick an uploaded
agreement PDF/XLSX (upload with `uploadWithProgress(file, "agreement", …)` from
`features/files/files-page.tsx`) → `POST /document-extractions` `ExtractionStart{file_id, kind:"agreement",
rerun}` (202 started, 200 existing). Poll detail while queued/processing. 409 `ai_not_enabled` → message + link to
`/admin/settings` for admins. 429 → message.

### 7. Extraction review — `/extractions/:id` (data.read)
`GET /document-extractions/{id}` → `ExtractionDetail` (status, confidence, error_code, error_detail, draft_id,
model, prompt_version, text_method, validation) with `result` typed as **`ExtractionResult`**: `fields[
ExtractionFieldView{key, critical, value, confidence, reasons[], evidence: ExtractionEvidence{page, sheet, cell,
quote, start, end, boxes}, pass_a, pass_b}]`, `blocking[]`, `needs_acknowledgement[]`, `indicators[
ExtractionIndicator]`, `notes{a[],b[]}`, `unresolved`. Field panel grouped critical first; per field: value,
confidence badge (text, not colour only), reasons, evidence quote with page/sheet/cell, both passes on expand;
conflict/unsupported fields visually distinct with their reason. Indicators (possible prompt injection, hidden
text) in a warning list. "Open draft" → `/agreement-drafts/:draft_id` (acknowledgements and approval happen
there through the S4 flow). Document view: link "Open document at page N" using the file download URL from
`GET /files/{file_id}` + `#page=N`. **Bounding-box highlight is not possible without a PDF renderer (none is
installed and none may be added): record it as a gap, do not fake it.**

### 8. AI usage — `/extractions/usage` (data.read)
`GET /ai/usage` → `UsageOut{enabled, model, today:UsageToday, daily[UsageDay]}` table.

### 9. Import mapping suggestion — not yours (moved to the imports owner; same file as their work).

## Verify (run as you go and all at the end; from `frontend/`)
- once: `npm ci`; then `npm run lint` · `npm run typecheck` · `npx vitest run src/features/calculations
  src/features/extractions` (then `npm test`)
- E2E (Docker running; API `localhost:8000`, Mailpit `localhost:8025`): `docker stop veyra-recover-frontend-1`,
  `npm run dev` (separate terminal), `$env:PW_CHANNEL="chrome"; npx playwright test e2e/calculations.spec.ts
  e2e/extractions.spec.ts`, then `npx playwright test`.
- E2E required: calculations — register admin + MFA, master data + one approved SPA agreement via the UI (reuse
  agreements.spec steps), import a sales CSV via the import wizard (reuse imports.spec steps), start a run, see it
  complete, filter exceptions, open one result's trace; axe on each page. Extractions — with AI disabled: start →
  `ai_not_enabled` message; usage page renders. (Live AI is not available: OD-2.)
- Unit tests for non-trivial pure logic (trace tree flattening, field grouping/sorting).

## If an endpoint is missing or differs
Do not fake, mock, or work around it. Record it in the report (endpoint, expected vs actual, screen), build the
rest, mark that part not done.
