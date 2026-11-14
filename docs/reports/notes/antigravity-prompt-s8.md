You are building the S8 reconciliation workbench of Veyra Recover (React 18 + TS + Vite + TanStack Query/Table + RHF/Zod + Tailwind/Radix).
SETUP (the branch and worktree already exist; do not create, merge or rebase anything):
  cd D:/veyra/veyra-ui-s8 && git pull --ff-only && cd frontend && npm ci
Work only in that worktree. Keep it brief: no narration and no file summaries. Act, then report once at the end.

READ ONLY: the brief `docs/reports/notes/ui-brief-s8.md`, the pattern files it lists (`features/claims/`,
`features/calculations/`, shared ui/common/api/session/routes, `files-page.tsx` upload), and in
`frontend/src/api/schema.d.ts` only the names the brief gives (search by name, never read it whole).

SCREENS (details in the brief): `/reconciliation` list · `/reconciliation/new` upload + ingest · `/reconciliation/:remittanceId`
(review with `total_required` → accept/void; workbench with T1–T5 suggestions confirm/reject using If-Match, manual
match by claim line or `claim_adjustment_id` with the server's 0.00 rule, categorise unmatched lines, run matching,
finalise with `uncategorised_lines` / `unresolved_suggestions` handling; findings) · `/reconciliation/reason-codes`.

HARD RULES
- Git: branch `ui/s8` only. One small commit per finished screen or test, then `git push`. Never push to main, merge,
  rebase, force-push or open a PR. Never bypass a commit hook (if it cannot run, write READY TO COMMIT in the report).
- Edit only `frontend/src/features/reconciliation/**`, the route and nav lines in `app/routes.tsx` / `app/app-layout.tsx`,
  `frontend/e2e/reconciliation-*.spec.ts`, `frontend/e2e/support.ts` (add helpers only) and `docs/reports/notes/ui-report-s8.md`.
  Never edit `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `frontend/src/api/`, `package.json`/lockfile or other docs.
- No business calculations in the UI: no allocation sums, remaining amounts, claimed − paid, total checks or
  pre-checks of the 0.00 rule. Show the server's strings exactly, with grouping only, and show server errors verbatim with `details[]`.
- No mock data, fixtures, MSW, stubbed responses, placeholder screens or TODOs. No hand-written API types: use the
  generated client and `components["schemas"]` only. Untyped `evidence` is rendered as a generic labelled tree.
- Every screen has loading, empty, error and 403 states, keyboard use, labels and a clean axe check (WCAG 2.1 AA).
- Missing or different endpoint: do not work around it. Record it as a gap (endpoint, expected vs actual, screen) and build the rest.

TESTS: Playwright on the real dev stack. The shared stack may be running another branch: first run
`curl -s http://localhost:8000/openapi.json | grep -c '"/api/v1/remittances'`; if it prints 0, write the specs, do not run them, and
report them as "not run (stack on another branch)"; the orchestrator runs them. Otherwise run them (API :8000, Mailpit :8025), using `e2e/support.ts` helpers. Remittance file
`testdata/generated/remittance/credit_memo_VOLT_CM-7781.csv`. Compare findings with `ground_truth/expected_reconciliation.json`
strings. Selectors use role, label or name only, scoped to a row, dialog or region. Run spec files in batches of at most 4.
On 429, stop and ask the owner to clear `rate_limit_counters`. Never skip, delete or loosen a test or assertion. A test
you could not run is reported as "not run".
VERIFY (from `frontend/`): after each screen run `npm run lint`, `npm run typecheck`, `npx vitest run src/features/reconciliation`
and its spec. At the end run `npm test` and full Playwright in batches of 4 or fewer.
REPORT `docs/reports/notes/ui-report-s8.md` (≤ 80 lines): screens done and not done, every command with pass/fail counts,
untested items, gaps, branch and last SHA. Commit and push it, then STOP.
