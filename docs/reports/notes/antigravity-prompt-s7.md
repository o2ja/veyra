You are building the S7 claim screens of an existing React app (Veyra Recover).
Worktree: `D:\veyra\veyra-ui-s7` (already on branch `ui/s7`). Work only there. Be brief: no narration, no
file summaries, no progress essays. Act, then report once at the end.

READ ONLY THESE (do not explore, list or search the rest of the project):
1. This prompt: `docs/reports/notes/antigravity-prompt-s7.md`.
2. The brief: `docs/reports/notes/ui-brief-s7.md` (screens, endpoints, rules, acceptance criteria, commands).
3. The example features to copy: `frontend/src/features/calculations/` and `frontend/src/features/agreements/`,
   plus the shared files the brief lists under "Pattern to copy", and e2e `calculations.spec.ts` + `support.ts`.
4. In `frontend/src/api/schema.d.ts`, only the types and paths the brief names (search by name; never read it whole).

HARD RULES
- Git: branch `ui/s7` only. One small commit per finished screen (or test), then `git push` right away.
  Never push to `main`, never merge, never rebase, never force-push, never open a PR.
- Edit only the folders the brief allows. Never touch `backend/`, `migrations/`, `infra/`, `testdata/`,
  `.github/`, `frontend/src/api/` (generated client), other feature folders, `package.json` or the lockfile, or
  `docs/` (except your report). No new dependencies.
- No mock data, fake or stubbed responses, MSW/fixtures standing in for the API, placeholder or "coming soon"
  screens, or TODO stubs. Everything uses the real API through the generated client and generated types only.
- No calculations in the UI: no sums of selected lines, totals, deadlines or eligibility logic. Show the server's
  values exactly as given (grouping/formatting only).
- Every screen handles loading, empty, error and permission-denied (403) states; keyboard accessible, labelled,
  axe-clean (WCAG 2.1 AA). Every confirm dialog is a real accessible dialog (focus trapped, Escape closes).
- Endpoint missing or different from the brief: do not work around it. Record it in the report as a gap
  (endpoint, expected vs actual, screen), build the rest, mark that part not done.
- The adjustments queue is out of scope.

VERIFY (from `frontend/`): after each screen run `npm run lint`, `npm run typecheck` and
`npx vitest run src/features/claims`; fix failures before the next screen. At the end run everything once:
`npm run lint`, `npm run typecheck`, `npm test`, then `npx playwright test e2e/claims.spec.ts`, then
`npx playwright test`. Never skip, delete or weaken a test; a test you could not run is reported as "not run".

FINAL REPORT: write `docs/reports/notes/ui-report-s7.md` (at most 80 lines), commit and push it on `ui/s7`:
- screens done (route, one line each) and screens not done, with why
- every verification command exactly as typed, with its result (pass/fail counts)
- what is unfinished or untested
- endpoint gaps
- branch name and last commit SHA
Then STOP. Do not merge and do not start other work.

If the repository's commit hook cannot run in your sandbox, never bypass it: `git add` the finished files and
append "READY TO COMMIT: <files> - <message>" to your report instead.
