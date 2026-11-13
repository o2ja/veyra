# Resume prompt (2026-11-13 evening): paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Resume the Veyra Recover build. The deadline is 2026-11-15.

Read ONLY these: this file, the SUMMARY at the top of `docs/reports/notes/overnight-log.md`, `docs/PROJECT_STATUS.md` §3, and your memory. Per-branch notes (`docs/reports/notes/<branch>-resume.md`, `<id>-*.md`) live on their branches; read one only when you work on that branch.

First run `git worktree list`, `git status`, `gh pr list` and `git fetch`. Commit and push any leftovers. Start the dev stack if it is down: `docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2 up -d` (from main).

## State (2026-11-13 evening)
- **Main:** 6a3e7a6 (#20 merged). GitHub Actions unpaid → merge gate = `make ci-local` 6/6 (CLAUDE.md §19). `gh pr merge` is blocked by the auto-mode classifier → the owner merges (or adds a permission rule).
- **#21 S11.1** `s11-audit-seal` 4f95e43: **ci-local 6/6 PASS** (comment on the PR), reviews passed → waits for the owner's merge. 4f95e43 also fixes the e2e flake (Vite reloaded the page when Playwright wrote junit-e2e.xml into frontend/).
- **#22 S11.3** `s11-security` a7f7f6a: reviewed; needs latest main + gate after #21. **#24** D-N8 (draft).
- **S7** `s7-claims` = `s7-adjustments` e43aefd: owner money rule D-N1/D-N3/D-N7 + D-N10 applied; 5th financial review **MERGEABLE**. PR #15 (draft): merge main in, gate, merge.
- **S8** `s8-reconciliation` 40dc42f: reviews 3–5 (`docs/reports/notes/s8-financial-review-{3,4,5}.md`): no financial discrepancy; serious (positive cross-batch adjustment allocation uncounted, §10.2) and all minors fixed; final pass MERGEABLE WITH MINORS, minors fixed + orchestrator-verified. Interim H2 = owner question D-N11. Merge after #15: merge main in, gate.
- **S9** `s9-recovery` adbe374 (0d8021b reviewed 3× + G3 D-N2 unpaid-deadline reminder test): merge final S8 in, review the D-N2 change with that merge, gate.
- **S10** `s10-reporting` e4715c5: owner figure decisions; 3 tests fail until real S8/S9 numbers are in. Rebase/merge S9 in, confirm those 3 pass, run the S10 reviewer.
- **Antigravity:** branches pre-built on their backend + `ui/s7` (briefs + prompts committed on each): `ui/s7-adjustments`, `ui/s8`, `ui/s9`, `ui/s10` (worktrees `D:/veyra/veyra-ui-*`). Its sandbox cannot reach localhost → it never runs Playwright; the orchestrator runs every UI branch's e2e. `ui/s7` (ac49bd7) and `ui/qa-1` round 2 (423d306) are done, not verified: `ui/qa-1` e2e needs main incl. #21 (dev DB is at the audit_seals head).
- **Other owner items:** D-N4 retention design, D-N8 ZAP/pen test, D-N9 Docker memory for S11.2, D-N11 (S8 H2).

## Next steps, in order
1. Owner merges #21. Then: merge main into `ui/qa-1`, run its e2e (`JOBS=e2e` ci-local script), fix or write a round-3 prompt.
2. #15 (S7): merge main in, ci-local 6/6, post, owner merges. Then S8 PR: merge main in, ci-local, merge. Then S9 (+D-N2 review), then S10 (+S10 reviewer). One at a time; full suite + negctl inside each gate.
3. #22 after #21 (merge main in, gate).
4. Verify each Antigravity UI branch when it reports (merge main in first; lint, typecheck, vitest, Playwright in batches of 4), reviewer, merge after its backend.
5. Remaining S11: S11.2 perf (D-N9), S11.4 a11y, S11.5 deploy (owner hosting), S11.8 pilot review. Then `docs/reports/RELEASE_1_REPORT.md`.
6. Housekeeping: `s11-docs-drill/backups/` holds a gitignored synthetic dump with password hashes; delete only if the owner agrees.

## Test helpers (session scratchpad, recreate if gone)
- One-off pytest in a worktree: see `docs/reports/notes/briefs/COMMON.md`; pass `-e REBATE_AI_WRITER_PASSWORD` and your own `REBATE_TEST_DB`. Tests reading `infra/` need `-v <worktree>/infra:/infra:ro` with `MSYS_NO_PATHCONV=1` in Git Bash.
- Negative control: `python -m tests.isolation.negative_control` (one agent at a time).

## Rules
- **Building:**
  - No mock data, fake responses, placeholders or TODO stubs.
  - Tests for all new behaviour.
  - Expected money values come from ground truth or hand derivation, never from code output.
  - The isolation suite and the RLS-off control (negctl) stay green and are extended for every new table and route.
- **Reviews:**
  - An independent financial review on all money logic.
  - One review pass per stage, plus a second pass on the fix diff only.
- **Merging:**
  - Merge gate: CLAUDE.md §19 (`make ci-local` 6/6 on the exact SHA, posted on the PR, after merging latest main).
  - The full suite once per merged wave.
  - Commit and push after every merged piece.
- **Agents:**
  - At most 2 at once (CLAUDE.md §19).
  - Use the cheaper model (sonnet) for builders and opus for reviewers.
  - Ponytail rules apply: shortest working diff, reuse before writing, no new dependencies or abstractions. They never relax quality.
- **Decisions:**
  - Rule-4 owner decisions: skip and log them, then continue.
  - **HARD STOP** on any financial discrepancy between ground truth, BUSINESS_LOGIC and code: report it to the owner and do not choose.
  - Never pass `--no-verify`, never force-push, never rewrite history.
- **No free model:** OmniRoute/OpenRouter has been removed. Use Claude only.
