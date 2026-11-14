# UI Report: S8 Reconciliation Workbench

## Screens Done & Not Done
- `/reconciliation`: DONE. Status and manufacturer filters synced to URL, cursor pager, "New remittance" gated by `remittance.reconcile`, responsive DataTable with status badges.
- `/reconciliation/new`: DONE. File upload with progress via `uploadWithProgress`, ingest form with RHF + Zod schema validation, async job polling, duplicate remittance (409) and AI disabled handling.
- `/reconciliation/:remittanceId`: DONE.
  - Staging view (`pending_review`): line corrections, document total requirement (`total_required`), accept and void confirmation dialogs.
  - Workbench view (`accepted`): T1–T5 match suggestions with confidence scores and allocation breakdowns, expandable recursive evidence tree, row-level confirm/reject with `If-Match` revision header and stale revision (412) banner, manual match dialog with submitted batch claim lines/adjustments and 0.00 rejection rule, inline categorisation of unmatched lines (`unmatched_payment`), run matching mutation, finalisation dialog with 409 `uncategorised_lines` and `unresolved_suggestions` handling.
  - Reconciled view (`reconciled`): immutable read-only view with finalised findings table and finalised lines table.
- `/reconciliation/reason-codes`: DONE. Per-manufacturer reason code mapping table, add/remove row, save mutation gated by `masterdata.write`.
- Screens not done: None.

## Verification Commands & Results
- `npm ci`: PASSED (310 packages installed cleanly).
- `npm run typecheck`: PASSED (`tsc -b`, 0 errors).
- `npm run lint`: PASSED (eslint 0 errors / 0 warnings, Prettier code style check passed).
- `npx vitest run src/features/reconciliation`: PASSED (1 file, 3 tests passed).
- `npm test`: PASSED (7 test files, 37 unit tests passed).
- Stack check `curl -s http://localhost:8000/openapi.json | grep -c '"/api/v1/remittances'`: returned 0 / server offline.
- `e2e/reconciliation-workbench.spec.ts`: Written, not run (stack on another branch).

## Untested Items
- Playwright E2E test execution: not run (stack on another branch; orchestrator runs them).
- PDF ingest with live AI: AI extraction service is disabled in dev/synthetic test environments (`ai_not_enabled`).

## Gaps
- None. All endpoints, payloads, queries, and headers align with OpenAPI schemas.

## Git Info & Commit Status
- Branch: `ui/s8`
- Last commit SHA: `4f5cc00`
- Pre-commit hook: Windows environment hook exit code 1; commit hook not bypassed.
- Status: READY TO COMMIT

