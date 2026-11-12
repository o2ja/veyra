You are fixing review findings on the S5/S6 screens you built in Veyra Recover. Worktree `D:\veyra\veyra-ui-s5s6`,
branch `ui/s5-s6` (already checked out; run `git pull` first: main was merged in and your work was committed).
Be brief: no narration. Act, then report once at the end.

READ ONLY THESE (no project exploration):
1. This prompt: `docs/reports/notes/antigravity-prompt-s5-s6-fixes.md`.
2. The fix list: `docs/reports/notes/ui-brief-s5-s6-fixes.md` (16 items).
3. From `docs/reports/notes/ui-brief-s5-s6.md` only the sections "Folders", "Rules" and "Verify".
4. The files each item names, `e2e/agreements.spec.ts` (for the rebate steps) and, in `frontend/src/api/schema.d.ts`,
   only the types you need (search by name).

HARD RULES (unchanged)
- Branch `ui/s5-s6` only; one small commit per item or group of items, `git push` after each. Never push to main,
  merge, rebase, force-push or open a PR.
- Edit only `frontend/src/features/calculations/**`, `frontend/src/features/extractions/**`,
  `frontend/src/features/agreements/draft-editor-page.tsx` (items 15-16 and the preview hook-in),
  `frontend/e2e/calculations*.spec.ts`, `frontend/e2e/extractions*.spec.ts`, and your report. Never touch `backend/`,
  `migrations/`, `infra/`, `testdata/`, `.github/`, `frontend/src/api/`, `frontend/src/components/`,
  `features/imports/`, `features/masterdata/`, `package.json`/lockfile, or other docs.
- No mock data, fake responses, placeholders or TODOs. No calculations or confidence logic in the UI.
- Loading, empty, error and permission-denied states stay handled; accessible (axe-clean).
- An item you cannot do properly: leave it, say why in the report.

VERIFY (from `frontend/`): after each item `npm run lint`, `npm run typecheck`,
`npx vitest run src/features/calculations src/features/extractions src/features/agreements`. At the end: `npm test`
and the full Playwright suite in batches of at most 4 spec files, clearing the dev rate-limit counters before each
batch (command in the fix list). Never skip, delete or weaken a test.

FINAL REPORT: append a section "Batch 2" to `docs/reports/notes/ui-report-s5-s6.md` (at most 50 lines): per item done /
not done (why), every command exactly as typed with pass/fail counts, what is untested, endpoint gaps, last commit
SHA. Commit and push it, then STOP.

If the commit hook cannot run in your sandbox, never bypass it: `git add` the files and append
"READY TO COMMIT: <files> - <message>" to the report.
