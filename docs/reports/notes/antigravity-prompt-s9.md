You are building the S9 recovery queue of Veyra Recover (React 18 + TS + Vite + TanStack Query/Table + RHF/Zod + Tailwind/Radix).
SETUP (the branch and worktree already exist; do not create, merge or rebase anything):
  cd D:/veyra/veyra-ui-s9 && git pull --ff-only && cd frontend && npm ci
Work only in that worktree. Keep it brief: no narration and no file summaries. Act, then report once at the end.

READ ONLY: the brief `docs/reports/notes/ui-brief-s9.md`, the pattern files it lists (`features/claims/`,
`agreements/draft-editor-page.tsx` If-Match/412, `features/calculations/` tree, `files-page.tsx` upload, shared
ui/common/api/session/routes), and in `frontend/src/api/schema.d.ts` only the names the brief gives (search by name).

SCREENS (details in the brief): `/recovery` queue (server order, unknown deadlines first, summary strip, filters in the URL) ·
`/recovery/:caseId` (amounts, deadline + basis, dispute assessment, timeline, notes, attachments, assign, change status,
reclaim → link to the S7 adjustments queue, write-off with `If-Match` + `Idempotency-Key` and the four-eyes propose/complete
flow, `reclaim_included`) · `/notifications` (reminders list, mark read).

HARD RULES
- Git: branch `ui/s9` only. One small commit per finished screen or test, then `git push`. Never push to main, merge,
  rebase, force-push or open a PR. Never bypass a commit hook (if it cannot run, write READY TO COMMIT in the report).
- Edit only `frontend/src/features/recovery/**`, the route and nav lines in `app/routes.tsx` / `app/app-layout.tsx`,
  `frontend/e2e/recovery-*.spec.ts`, `frontend/e2e/support.ts` (add helpers only) and `docs/reports/notes/ui-report-s9.md`.
  Never edit `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `frontend/src/api/`, `package.json`/lockfile or other docs.
- No business calculations in the UI: no days-left countdown, no deadline maths, no variance − recovered, no client
  state machine, no client re-sorting of the queue, no reminder creation. Show the server's values exactly.
- No mock data, fixtures, MSW, stubbed responses, placeholder screens or TODOs. No hand-written API types: use the
  generated client and `components["schemas"]` only. Untyped objects are rendered as a generic labelled tree.
- Every screen has loading, empty, error and 403 states, keyboard use, labels and a clean axe check (WCAG 2.1 AA).
- Missing or different endpoint: do not work around it. Record it as a gap (endpoint, expected vs actual, screen) and build the rest.

TESTS: Playwright on the real dev stack. The shared stack may be running another branch: first run
`curl -s http://localhost:8000/openapi.json | grep -c '"/api/v1/cases'`; if it prints 0, write the specs, do not run them, and
report them as "not run (stack on another branch)"; the orchestrator runs them. Otherwise run them (API :8000, Mailpit :8025), using `e2e/support.ts` helpers. Setup reuses the
S8 finalise steps (CM-7781 → four cases). Four-eyes uses two approvers created via invitations (support helpers).
Selectors use role, label or name only, scoped to a row, dialog or region. Run spec files in batches of at most 4. On 429,
stop and ask the owner to clear `rate_limit_counters`. Never skip, delete or loosen a test or assertion. A test you could
not run is reported as "not run".
VERIFY (from `frontend/`): after each screen run `npm run lint`, `npm run typecheck`, `npx vitest run src/features/recovery`
and its spec. At the end run `npm test` and full Playwright in batches of 4 or fewer.
REPORT `docs/reports/notes/ui-report-s9.md` (≤ 80 lines): screens done and not done, every command with pass/fail counts,
untested items, gaps, branch and last SHA. Commit and push it, then STOP.
