# Antigravity prompt: QA-1 fixes (branch ui/qa-1, worktree D:/veyra/veyra-ui-qa1)

Pull first: the orchestrator merged origin/main into ui/qa-1 (ec83e66) and committed your pending report line.

Orchestrator verification 2026-11-13 (real stack, Vite from your worktree, Chrome, batches of 4, rate_limit_counters cleared per batch):
lint, typecheck, vitest (30) PASS. **Playwright FAIL: 8 of 18 specs.**

| Spec | First error |
|---|---|
| qa-admin | strict mode violation: `getByText('<invited email>')` resolves to 2 elements (scope the locator to the table row / dialog) |
| qa-agreements | `getByText('Validation passed.')` not found (check the real validation message the server/editor shows) |
| qa-auth | `toBeVisible` element not found |
| qa-calculations | `toBeVisible` element not found |
| qa-files | axe: `definition-list` violation on `/files/:fileId` (`dl` must contain only `dt`/`dd` groups) — a real a11y bug in file-detail-page.tsx |
| qa-imports | `toBeVisible` element not found |
| a11y (existing) | `locator.click` timeout — check whether your changes moved a control the S1 a11y walk clicks; it passes on main in CI |
| qa-extractions, qa-home, qa-masterdata | pass |

Do: fix each failing spec against what the real UI renders (never loosen an assertion to nothing; fix the UI when the bug is in the UI, e.g. the `dl` structure). Selectors by role/label, scoped to the region. Re-run `npx playwright test <spec>` for each failing spec if your sandbox allows; if it does not, say so in the report. Update `docs/reports/notes/ui-report-qa-1.md` (section "Fixes round 2") and push. Allowed files: the same as QA-1 (`frontend/src/features/**`, `frontend/e2e/qa-*.spec.ts`, `frontend/e2e/support.ts`, your report).
