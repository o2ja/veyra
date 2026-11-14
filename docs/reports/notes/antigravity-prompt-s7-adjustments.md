You are finishing the S7 claim UI of Veyra Recover (React 18 + TS + Vite + TanStack Query/Table + RHF/Zod + Tailwind/Radix).
SETUP (the branch and worktree already exist; do not create, merge or rebase anything):
  cd D:/veyra/veyra-ui-s7-adjustments && git pull --ff-only && cd frontend && npm ci
Work only in that worktree. Keep it brief: no narration and no file summaries. Act, then report once at the end.

READ ONLY: the brief `docs/reports/notes/ui-brief-s7-adjustments.md`, `frontend/src/features/claims/**`, the shared files
the brief lists, and in `frontend/src/api/schema.d.ts` only the names the brief gives (search by name, never read it whole).

TASKS (details in the brief):
0. Run `npx playwright test e2e/claims.spec.ts` on the real stack (it was never run). Fix UI bugs only.
1. Adjustments queue `/claims/adjustments`: status tabs, type and manufacturer filters, approve/reject with
   four-eyes gating, and the `cannot_net_claim` message kept on the row.
2. Batch builder: carry approved adjustments (`include_adjustment_ids`), plus a rebate accrual mode (`accrual_entry_ids`).
3. Batch detail: carried adjustments table, Source column, and the server `total_amount` labelled as including adjustments.
4. `line_type` added to the export template sources.

HARD RULES
- Git: branch `ui/s7-adjustments` only. One small commit per finished screen or test, then `git push`. Never push to
  main, merge, rebase, force-push or open a PR. Never bypass a commit hook (if it cannot run, write READY TO COMMIT in the report).
- Edit only `frontend/src/features/claims/**`, the route and nav lines in `app/routes.tsx` / `app/app-layout.tsx`,
  `frontend/e2e/claims*.spec.ts`, `frontend/e2e/support.ts` (add helpers only) and `docs/reports/notes/ui-report-s7-adjustments.md`.
  Never edit `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `frontend/src/api/`, `package.json`/lockfile or other docs.
- No business calculations in the UI: no sums, subtotals, net amounts, sign checks or eligibility logic. Show the
  server's decimal strings exactly, with grouping only.
- No mock data, fixtures, MSW, stubbed responses, placeholder screens or TODOs. No hand-written API types: use the
  generated client and `components["schemas"]` only.
- Every screen has loading, empty, error and 403 states, keyboard use, labels and a clean axe check (WCAG 2.1 AA).
- Missing or different endpoint: do not work around it. Record it as a gap (endpoint, expected vs actual, screen) and build the rest.

TESTS: Playwright on the real dev stack. The shared stack may be running another branch: first run
`curl -s http://localhost:8000/openapi.json | grep -c '"/api/v1/claim-adjustments'`; if it prints 0, write the specs, do not run them, and
report them as "not run (stack on another branch)"; the orchestrator runs them. Otherwise run them (API :8000, Mailpit :8025), using `e2e/support.ts` helpers (per-test IP).
Selectors use role, label or name only, scoped to a row, dialog or region. Run spec files in batches of at most 4. On 429,
stop and ask the owner to clear `rate_limit_counters`. Never skip, delete or loosen a test or assertion. A test you could
not run is reported as "not run".
VERIFY (from `frontend/`): after each task run `npm run lint`, `npm run typecheck`, `npx vitest run src/features/claims` and
the claims specs. At the end run `npm test` and full Playwright in batches of 4 or fewer.
REPORT `docs/reports/notes/ui-report-s7-adjustments.md` (≤ 80 lines): screens done and not done, every command with
pass/fail counts, untested items, gaps, branch and last SHA. Commit and push it, then STOP.
