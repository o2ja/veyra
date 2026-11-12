You are doing a quality pass over every existing screen of a React app (Veyra Recover). Be brief: no narration,
no file summaries, no progress essays. Act, then report once at the end.

STEP 0, before anything else:
- In D:\veyra\veyra-ui-s7, make sure your ui/s7 work and docs/reports/notes/ui-report-s7.md are committed and pushed (git status clean, git log origin/ui/s7 shows your last commit). If not, commit and push them first.
- Then, in D:\veyra\rebate-recovery-docs\rebate-recovery: git fetch origin, then git worktree add D:\veyra\veyra-ui-qa1 -b ui/qa-1 origin/main.
- Work ONLY in D:\veyra\veyra-ui-qa1 on branch ui/qa-1.

READ ONLY THESE (do not explore, list or search the rest of the project):
1. This prompt: docs/reports/notes/antigravity-prompt-qa-1.md.
2. The brief: docs/reports/notes/ui-brief-qa-1.md (scope, per-screen checklist, folders, acceptance, commands).
3. The example: frontend/src/features/calculations/, e2e/calculations.spec.ts and e2e/support.ts.
4. The feature folder you are working on at that moment.
5. In frontend/src/api/schema.d.ts, only the types and paths you need (search by name; never read it whole).

HARD RULES
- Git: branch ui/qa-1 only. One small commit per finished feature folder (fixes + its qa spec), then git push right away.
  Never push to main, never merge, never rebase, never force-push, never open a PR.
- Edit only the folders the brief allows. Never touch backend/, migrations/, infra/, testdata/, .github/,
  frontend/src/api/ (generated client), features/claims/, package.json or the lockfile, or docs/ (except your report).
  No new dependencies.
- No mock data, fake or stubbed responses, MSW/fixtures standing in for the API, placeholders or TODO stubs.
  Use the real API through the generated client only.
- No money calculations in the UI: no sums, totals, rounding or eligibility logic. Show the server's values exactly
  (formatting only, with the existing helper).
- Every screen handles loading, empty, error and permission-denied (403) states, accessibly (WCAG 2.1 AA, axe-clean,
  keyboard usable, labelled, real accessible dialogs).
- A problem that needs a backend change: do not work around it. Record it in the report as a gap (endpoint,
  expected vs actual, screen) and move on.
- Never skip, delete or weaken an existing test.

VERIFY (from frontend/):
- After each feature: npm run lint, npm run typecheck, npx vitest run src/features/<feature>, and
  npx playwright test e2e/qa-<feature>.spec.ts. Fix failures before the next feature.
- At the end, run everything once: npm run lint, npm run typecheck, npm test, npx playwright test.
- A test you could not run is reported as "not run".

FINAL REPORT: write docs/reports/notes/ui-report-qa-1.md (at most 100 lines), commit and push it on ui/qa-1:
- per feature: fixes made (screen → what changed) and the qa spec added
- every verification command exactly as typed, with pass/fail counts
- backend gaps found
- what is unfinished or untested
- branch name and last commit SHA
Then STOP. Do not merge and do not start other work.

If the repository's commit hook cannot run in your sandbox, never bypass it: git add the finished files and append
"READY TO COMMIT: <files> - <message>" to your report instead.
