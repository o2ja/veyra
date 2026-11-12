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

