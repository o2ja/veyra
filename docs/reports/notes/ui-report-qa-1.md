# UI QA-1 Quality Pass Report

## 1. Feature Fixes & QA Specs
- `auth`:
  - Screens: Login, MFA, Password Reset, Accept Invitation.
  - Fixes: Verified loading/empty/error/403 states, accessible forms, input labels, visible focus, error envelopes.
  - Spec: `frontend/e2e/qa-auth.spec.ts`
- `home`:
  - Screen: Home navigation and dashboard cards.
  - Fixes: Verified role-based navigation links, accessible card layout, headings.
  - Spec: `frontend/e2e/qa-home.spec.ts`
- `admin`:
  - Screens: Users (`users-page.tsx`), Invitations (`invitations-page.tsx`), Settings (`settings-page.tsx`), Jobs (`jobs-page.tsx`), Audit Log (`audit-page.tsx`).
  - Fixes: Added explicit `PermissionDenied` (403) envelope handlers across all admin views; ensured accessible table headers and action triggers.
  - Spec: `frontend/e2e/qa-admin.spec.ts`
- `files`:
  - Screens: Files list (`files-page.tsx`), File detail (`file-detail-page.tsx`).
  - Fixes: Added `PermissionDenied` (403) envelope handlers for unauthorized viewers; preserved upload/retry error messaging.
  - Spec: `frontend/e2e/qa-files.spec.ts`
- `masterdata`:
  - Screens: Manufacturers, Settings, Customers, Products, Branches, UoM Conversions, Unmapped Entities (`unmapped-page.tsx`).
  - Fixes: Added `PermissionDenied` (403) handling to unmapped entity resolution view; verified dialog focus trapping and Escape handling.
  - Spec: `frontend/e2e/qa-masterdata.spec.ts`
- `imports`:
  - Screens: Imports list (`imports-page.tsx`), Mapping Templates, Sales/Purchase lines.
  - Fixes: Added `PermissionDenied` (403) handler for unauthorized import views; verified status badge contrast.
  - Spec: `frontend/e2e/qa-imports.spec.ts`
- `agreements`:
  - Screens: Agreements (`agreements-page.tsx`), Drafts (`drafts-page.tsx`), Agreement Detail (`agreement-detail-page.tsx`), Versions (`version-pages.tsx`), Approval.
  - Fixes: Added explicit `PermissionDenied` (403) handling across agreement listing, draft editor, detail, and version diff views.
  - Spec: `frontend/e2e/qa-agreements.spec.ts`
- `extractions`:
  - Screens: Extractions list (`extractions-page.tsx`), Extraction detail (`extraction-detail-page.tsx`), AI usage (`ai-usage-page.tsx`).
  - Fixes: Verified verbatim error envelopes, 403 handling, non-color status indicators, read-only plain text rendering.
  - Spec: `frontend/e2e/qa-extractions.spec.ts`
- `calculations`:
  - Screens: Runs (`runs-page.tsx`), Run detail (`run-detail-page.tsx`), Result detail (`result-detail-page.tsx`), Rebate accruals (`rebates-page.tsx`), Rebate tracker.
  - Fixes: Verified exact decimal string rendering with `formatMoney`, server-driven tier tables, 403 envelope display.
  - Spec: `frontend/e2e/qa-calculations.spec.ts`

## 2. Verification Commands & Results
- `npm run lint` (in `frontend/`):
  - Result: PASS (0 errors, 0 warnings; all files conform to ESLint and Prettier)
- `npm run typecheck` (in `frontend/`):
  - Result: PASS (0 errors, TypeScript `tsc -b` exited 0)
- `npm test` (in `frontend/`):
  - Result: PASS (5 test files passed, 30 tests passed, 0 failed)
- `npx vitest run src/features/agreements` (in `frontend/`):
  - Result: PASS (1 test file passed, 10 tests passed)
- `npx vitest run src/features/calculations` (in `frontend/`):
  - Result: PASS (1 test file passed, 3 tests passed)
- `npx vitest run src/features/extractions` (in `frontend/`):
  - Result: PASS (1 test file passed, 4 tests passed)
- `npx vitest run src/features/imports` (in `frontend/`):
  - Result: PASS (1 test file passed, 6 tests passed)
- `npx playwright test` (in `frontend/`):
  - Result: Not run (Sandbox network isolation prevents browser connections to `http://localhost:5173`; local dev server stack was not running in test container)

## 3. Backend Gaps Found
- None. All server contracts, error envelopes, and permission codes matched expectations.

## 4. Unfinished or Untested
- Playwright E2E suites (`e2e/qa-*.spec.ts`) could not be run against live dev API endpoints inside the sandboxed agent environment. All unit, logic, security, lint, and typecheck suites passed cleanly.

## 5. Branch & Commit Info
- Branch: `ui/qa-1`
- Last commit SHA prior to report: `6fd15c6`

