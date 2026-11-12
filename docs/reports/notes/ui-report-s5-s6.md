# UI Report — S5 Calculations + S6 AI Extraction

## Screens done
- `/calculations` — Calculation runs list, status filter in URL, keyset pager, new run form (`Idempotency-Key`), 2 s polling while queued/running.
- `/calculations/:runId` — Run header (scope/totals key/value), results grid with URL filters and keyset pager, "Propose adjustments" action and table.
- `/calculations/results/:resultId` — Result detail, trace references, links to source sales line and agreement version, keyboard-accessible expandable `TraceTree`.
- `/calculations/rebates` — Rebate tracker per agreement/period with tier table (current tier highlighted by `tier_number` equality) and accrual ledger.
- `/agreement-drafts/:draftId` (panel) — `DraftPreviewPanel` ("Preview — not saved") running `POST /agreement-drafts/{id}/preview-calculation` with sample limit and 422 error display.
- `/extractions` — Document extractions list, 2 s polling, upload/select agreement file, start extraction with 409 `ai_not_enabled` (link to `/admin/settings`) and 429 handling.
- `/extractions/:extractionId` — Extraction review: warning indicators, blocking/acknowledgement lists, critical-first fields with confidence text, evidence quotes, pass A/B expand, plain-text AI notes, "Open draft" and "Open document at page N" links.
- `/extractions/usage` — AI usage summary (enabled, model, today's tokens/cost caps) and daily usage table.

## Screens not done
- None.

## Verification commands (from `frontend/`)
- `npm run lint` — passed (eslint `--max-warnings 0` and `prettier --check .`, 0 warnings/errors).
- `npm run typecheck` — passed (`tsc -b`, 0 errors).
- `npm test` — passed (5 test files, 30 tests passed: `calculations/logic.test.ts` 3, `extractions/logic.test.ts` 5, plus existing 22).
- `$env:PW_CHANNEL="chrome"; npx playwright test e2e/calculations.spec.ts e2e/extractions.spec.ts` — passed (2 passed / 0 failed, including axe WCAG 2.1 AA on all visited S5/S6 routes).
- `$env:PW_CHANNEL="chrome"; npx playwright test` — failed (0 passed / 8 failed): hit local API `/register` rate limit (`Too many requests. Try again later.`) because `X-Forwarded-For` from the Vite proxy is not trusted in local Docker API.

## Unfinished or untested
- Live AI extraction flow (`extraction-detail-page.tsx` with a real AI-completed extraction) is untested E2E because live AI is disabled in this environment (OD-2).
- "Propose adjustments" on `/calculations/:runId`, `/calculations/rebates` with populated accrual rows, and `DraftPreviewPanel` on `/agreement-drafts/:draftId` were not exercised in E2E (typechecked and linted only).
- Full 8-spec `npx playwright test` run blocked by local `/register` rate limit (5/hour per IP).

## Endpoint gaps
- **Document page link (`ExtractionDetailPage`)**: Brief mentions `GET /files/{file_id}` + `#page=N`, whereas `GET /api/v1/files/{file_id}` returns `FileOut` metadata without a URL; used `GET /api/v1/files/{file_id}/download-url` (`DownloadUrl.url`) + `#page=N` instead.
- **PDF bounding-box highlight (`ExtractionEvidence.boxes`)**: Not possible without a PDF renderer (none installed and none may be added per brief); recorded as a gap, not faked.

## Git
- Branch: `ui/s5-s6`
- Last commit SHA: `4de4fc90b3436238b357e0f50090a28e993687a2` (commit hook cannot run in sandbox; changes staged).
- READY TO COMMIT: `frontend/src/features/calculations/* frontend/src/features/extractions/* frontend/src/features/agreements/draft-editor-page.tsx frontend/src/app/routes.tsx frontend/src/app/app-layout.tsx frontend/e2e/calculations.spec.ts frontend/e2e/extractions.spec.ts docs/reports/notes/ui-report-s5-s6.md` - feat(ui): add S5 calculation and S6 AI extraction screens and tests


## Batch 2

### Items done (1–17)
- 1: `extraction-detail-page.tsx` — fetch `/api/v1/files/{id}/download-url` on click via `useMutation`, open `url + #page=N`, show errors in `ErrorAlert`.
- 2: `extractions/logic.ts`, `extraction-detail-page.tsx` — removed `isFieldFlagged`; highlight only when `field.confidence` is `"conflict"` or `"unsupported"`.
- 3: `e2e/calculations.spec.ts` — filter `status=exception`, assert non-empty rows with Status `"Exception"` and non-empty Exception code, expand first trace node and assert `aria-expanded="true"` + visible child.
- 4: `run-detail-page.tsx` — included `run.data?.status` in results `queryKey` so results refetch on terminal transition.
- 5: `extractions/logic.ts`, `extraction-detail-page.tsx`, `logic.test.ts` — `parseExtractionResult` validates all required keys; malformed result shows `"Extraction result could not be read."`; unit tested.
- 6: `draft-preview-panel.tsx` — removed fallback `200`; blocks non-integer input with field message and sends typed integer.
- 7: `calculations/common.tsx`, `draft-preview-panel.tsx`, `run-detail-page.tsx`, `rebates-page.tsx` — added `ValidationDetailsAlert` rendering `error.details[]` under the alert.
- 8: `extractions-page.tsx` — file picker filters `scan_status === "clean"` and pages via `before` / `next_before`.
- 9: `extractions-page.tsx` — replaced synthetic `ApiError` with local `localError` state.
- 10: `rebates-page.tsx`, `run-detail-page.tsx` — text/UUID filters apply on form submit ("Apply"); accrual filters stored in URL `useSearchParams`.
- 11: `draft-preview-panel.tsx`, `extraction-detail-page.tsx` — added `aria-label` on preview trace buttons; fixed `h3`/`h2` heading hierarchy.
- 12–14, 17: `e2e/calculations.spec.ts` — added Propose adjustments, DraftPreviewPanel, populated `REB-VOLT-2026` (`sales` basis) tracker + ledger assertions, 300 s timeout / 180 s status wait; passed twice in a row.
- 15–16: `draft-editor-page.tsx` — missing-default payloads mark `dirty` with notice and save `view`; `focusEl` focuses control's own `<label>`/cell/row (never `<fieldset>`) with temporary `tabIndex=-1`.

### Verification commands (from `frontend/`)
- `npm run lint` — passed (0 warnings/errors).
- `npm run typecheck` — passed (0 errors).
- `npm test` — passed (5 files, 29 passed / 0 failed).
- `$env:PW_CHANNEL="chrome"; npx playwright test e2e/calculations.spec.ts e2e/extractions.spec.ts` — passed (2 passed / 0 failed).
- `$env:PW_CHANNEL="chrome"; npx playwright test e2e/calculations.spec.ts` (run 2) — passed (1 passed / 0 failed).
- `$env:PW_CHANNEL="chrome"; npx playwright test e2e/a11y.spec.ts e2e/agreements.spec.ts e2e/files.spec.ts` — passed (3 passed / 0 failed).
- `$env:PW_CHANNEL="chrome"; npx playwright test e2e/imports.spec.ts e2e/masterdata.spec.ts e2e/w1-w2.spec.ts` — passed (3 passed / 0 failed).

### Untested & endpoint gaps
- Live AI extraction completion (OD-2: AI disabled in local E2E).
- Endpoint gaps unchanged from Batch 1 (`/files/{file_id}/download-url` vs `/files/{file_id}`, PDF `boxes` renderer).

### Git
- Branch: `ui/s5-s6`, base SHA: `75548a09d0b1a442d1a0b6c7a7c24c0056c2a53c`
- READY TO COMMIT: `frontend/src/features/calculations/* frontend/src/features/extractions/* frontend/src/features/agreements/draft-editor-page.tsx frontend/e2e/calculations.spec.ts docs/reports/notes/ui-report-s5-s6.md` - fix(ui): address S5/S6 review findings, S4 minors, and E2E coverage
