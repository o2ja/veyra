You are building the S5 calculation + S6 AI-extraction frontends for an existing React app. Work in the git worktree
`D:\veyra\veyra-ui-s4` (already on branch `ui/s5-s6`). Be brief: do not narrate, do not summarise files back,
no progress essays. Act, then report once at the end.

READ ONLY THESE, nothing else (do not explore or list the project):
1. `docs/reports/notes/ui-brief-s5-s6.md` — your complete spec (screens, endpoints, rules, commands, criteria).
2. The pattern files the brief names under "Pattern to copy" (masterdata folder, ui.tsx, lib/api, session,
   routes, app-layout, files-page `uploadWithProgress`, e2e masterdata/support/w1-w2, imports/logic.test.ts).
3. In `frontend/src/api/schema.d.ts`, only the types the brief names (search by name; do not read it whole).
4. Only the two test-data files the brief names, for the e2e test.

RULES (hard):
- Git: branch `ui/s5-s6` only. Small commits (one per screen or test), clear messages. Never push to `main`,
  never merge, never rebase `main`, never force-push. You may push `ui/s5-s6`.
- Edit only the folders the brief allows. Never touch `backend/`, `migrations/`, `infra/`, `testdata/`,
  `.github/`, `docs/` (except your report), `frontend/src/api/` (generated client), `package.json` or the
  lockfile. No new dependencies.
- No mock data, no fake or stubbed API responses, no MSW/fixtures standing in for the API, no placeholder or
  "coming soon" screens, no TODO stubs. Everything talks to the real API through the generated client.
- No business calculations in the UI (no amounts, no rate maths, no validity/overlap/eligibility logic).
  Display the server's values as given. The server decides validation and permissions.
- Use only generated types; never hand-write API types.
- If an endpoint is missing or behaves differently from the brief: do not work around it. Record it in the
  report, finish the rest, and mark that part not done.

VERIFY AS YOU GO (from `frontend/`): after each screen run `npm run lint`, `npm run typecheck` and
`npx vitest run src/features/calculations src/features/extractions`. Fix failures before moving on. At the end run everything once:
`npm run lint`, `npm run typecheck`, `npm test`, and the Playwright steps in the brief
(`npx playwright test e2e/calculations.spec.ts e2e/extractions.spec.ts`, then `npx playwright test`). Do not skip, delete or weaken
tests to get green; a test you could not run is reported as not run.

FINAL REPORT: write `docs/reports/notes/ui-report-s5-s6.md` (≤80 lines), commit it on `ui/s5-s6`, push `ui/s5-s6`:
- screens finished (route, one line each) and screens not finished, with why
- every verification command you ran, exactly as typed, with its result (pass/fail counts)
- what you could not finish or could not test
- endpoint gaps (endpoint, expected vs actual, affected screen)
- branch name and last commit SHA

Then STOP. Do not merge, do not open a PR into main, do not start other work.

NOTE: your sandbox may not run the repository's commit hook. If `git commit` fails for that reason, never bypass it:
stage each finished screen with `git add <files>` and append "READY TO COMMIT: <files> — <message>" to your report.
