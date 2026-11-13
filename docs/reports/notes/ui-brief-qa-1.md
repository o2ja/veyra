# UI brief QA-1: quality pass over every merged screen (branch `ui/qa-1`, from latest `main`)

## Why this task
Every endpoint on main already has a screen. The S7 adjustments, S8 workbench, S9 queue and S10 dashboard screens wait for their backends to merge; they come **later**. This task makes the existing screens production-grade and fully covered by tests. It adds no new endpoints and no new features.

## Screens in scope (all on main)
| Feature folder | Screens |
|---|---|
| `auth` | login, MFA, password reset, invitations accept |
| `admin` | users, invitations, settings (`/api/v1/settings`), audit log (`/api/v1/audit-log`) |
| `masterdata` | manufacturers (+ settings), customers, products, branches, UoM conversions, unmapped entities (+ resolve) |
| `files` | files, jobs |
| `imports` | imports, mapping templates, sales/purchase lines |
| `agreements` | agreements, drafts, versions, approval |
| `extractions` | document extractions, review |
| `calculations` | calculation runs, results, rebate accruals, rebate tracker |
| `home` | home |

## What to do, per screen
1. **States:** loading, empty, error (API error envelope message shown, no internals) and permission-denied (403 shows a clear message, not a blank page). Fix any that are missing.
2. **Accessibility (WCAG 2.1 AA):**
   - Every input has a label.
   - Keyboard-only use works: tab order and visible focus.
   - Dialogs trap focus and close on Escape.
   - Tables have headers.
   - Colour is never the only signal.
   - axe is clean.
3. **Permissions UI:** hide or disable actions the role cannot use, matching the server's permission for each route. The server stays the authority: never remove a server call to "simulate" a permission.
4. **Money display:** server decimal strings shown exactly, formatted only with the existing helper. No arithmetic, sums or rounding in the UI.
5. **Pagination:** use the cursor pager everywhere a list endpoint is paginated, and never load a whole list.
6. **Tests:**
   - One Playwright spec per feature, `e2e/qa-<feature>.spec.ts` (new). Each spec covers the main happy path, the empty state, an error state, 403 for a role without permission (`viewer` or `analyst`, via the support helpers), and an axe check on each page.
   - Use the real dev API only.
   - Use the support.ts helpers for users and X-Forwarded-For.
7. **Findings you must not fix:** if a screen needs a backend change (a missing field, a wrong error code, a missing permission), do NOT work around it. Record it in the report as a gap: endpoint, expected vs actual, screen.

## Example to copy
`frontend/src/features/calculations/` (states, pager, permissions, money formatting) and `e2e/calculations.spec.ts` + `e2e/support.ts`.

## Folders
- **May edit:** `frontend/src/features/**` (fixes only), shared UI in `frontend/src/components/**` (only to fix a11y/states bugs; keep the API the same), `frontend/e2e/qa-*.spec.ts` (new), `frontend/e2e/support.ts` (add helpers only, never change existing ones), `docs/reports/notes/ui-report-qa-1.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `frontend/src/api/` (generated), `package.json`/lockfile, `features/claims/**` (S7 is on ui/s7), other `docs/`. No new dependencies.

## Acceptance
- Every in-scope screen handles all four states.
- axe is clean on every page.
- Every feature has its qa spec and it passes.
- `npm run lint`, `npm run typecheck` and `npm test` pass.
- Full `npx playwright test` passes.
- The report lists every fix (screen → what changed), every gap, and anything not run.

## Commands (from `frontend/`)
- **After each feature:** `npm run lint`, `npm run typecheck`, `npx vitest run src/features/<feature>`, `npx playwright test e2e/qa-<feature>.spec.ts`.
- **At the end:** `npm run lint`, `npm run typecheck`, `npm test`, `npx playwright test`.
- **Rate-limit errors in e2e:** run specs in batches of at most 4, and ask the owner to clear rate_limit_counters between batches; never disable limits.
