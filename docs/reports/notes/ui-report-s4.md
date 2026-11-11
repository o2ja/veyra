# S4 UI Report — Agreements Frontend

## Screens finished
- `/agreements` (`frontend/src/features/agreements/agreements-page.tsx`): agreements list with manufacturer/type filters, keyset pagination, cancelled status, and New draft link.
- `/agreements/:agreementId` (`frontend/src/features/agreements/agreement-detail-page.tsx`): agreement detail, version history, amendment prefill, version comparison, 404 state, and cancel dialog.
- `/agreements/versions/:versionId` & `/agreements/versions/:versionId/diff/:otherVersionId` (`frontend/src/features/agreements/version-pages.tsx`): version detail (rules, customers, tiers, server-paged items) and diff table.
- `/agreement-drafts` (`frontend/src/features/agreements/drafts-page.tsx`): drafts list with URL status filter (`pending_approval` default for approvers, else `draft`) and manual draft creation.
- `/agreement-drafts/:draftId` (`frontend/src/features/agreements/draft-editor-page.tsx`): draft editor (SPA/Rebate rules, customers, 100-row paged items, tiers, unsaved-changes guard, validation issue focus, bulk items import preview/apply, submit with warning acks, four-eyes approve/return/reject, 412 reload).

## Screens not finished
- None (all 5 screens implemented; live E2E runtime verification not executed in sandbox).

## Verification commands run (from `frontend/`)
- `npm ci`: passed (added 310 packages).
- `npm run lint` (`eslint . --max-warnings 0 && prettier --check .`): passed (0 errors, 0 warnings).
- `npm run typecheck` (`tsc -b`): passed.
- `npx vitest run src/features/agreements`: passed (1 file, 6 tests passed).
- `npm test` (`vitest run`): passed (3 files, 18 tests passed).
- `npx playwright test e2e/agreements.spec.ts`: NOT RUN (sandbox blocks `localhost:8000`, `localhost:8025`, `localhost:5173`).
- `npx playwright test`: NOT RUN (same sandbox network restriction).

## Not finished / not tested
- Live Playwright E2E execution (`frontend/e2e/agreements.spec.ts` is written, formatted, and typechecked, but not executed against Docker).
- `git commit` and `git push` (sandbox cannot run Git for Windows `sh.exe` pre-commit hook or network push).

## Endpoint / schema gaps
- `POST /api/v1/files` (`Body_file_upload.kind`): brief specifies `uploadWithProgress(file, "spreadsheet", …)` for bulk items import (`draft-editor-page.tsx`), but `schema.d.ts` and `files-page.tsx` only permit `"import" | "agreement" | "remittance" | "attachment"` (`"import"` accepts `.csv,.xlsx`). Used `"import"` to match generated types; needs live API verification.

## Git state
- Branch: `ui/s4`
- Last commit SHA: `85b3a19a961ca4faa93484b6d5454ea2cf3e552e`

