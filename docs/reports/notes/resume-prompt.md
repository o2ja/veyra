# Resume prompt (2026-11-13 morning): paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Resume the Veyra Recover build. The deadline is 2026-11-15.

Read ONLY these: this file, the MORNING SUMMARY at the top of `docs/reports/notes/overnight-log.md`, `docs/PROJECT_STATUS.md` §3, and your memory. Per-branch notes (`docs/reports/notes/<branch>-resume.md`, `<id>-*.md`) live on their branches; read one only when you work on that branch.

First run `git worktree list`, `git status`, `gh pr list` and `git fetch`. Commit and push any leftovers. Start the dev stack if it is down: `docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2 up -d` (from main).

## State (2026-11-13 morning)
- **Main:** #18 merged (c7d014b); ADR-017 + `docs/PRODUCTION_PLAN.md` (managed S3 in production, owner decision).
- **CI is dead:** GitHub Actions refuses every job for billing (D-N0). Check first: `gh pr checks 20`. Nothing merges without 6/6 green.
- **Ready PRs (reviewed, findings fixed), merge when CI is green:** #20 `s11-docs-drill` (restore drill + guides), #21 `s11-audit-seal` (S11.1; migration 20261115_0900_audit_seals on calc_runs), #22 `s11-security` (S11.3).
- **Held by financial HARD STOPs (owner decides; do not pick a side):**
  - D-N1 S7: `s7-adjustments` 17a70c9 → `s7-claims` → PR #15.
  - D-N3 S8: `s8-reconciliation` edaa230 (contains s7-adjustments + main; remittances re-chained after neg_claims_guard; 1 head).
  - D-N7 S10: `s10-reporting` c9f971f (stacked on S9 7fe11bb; merge s9-recovery 0d8021b in before PR).
  - All three are one question: which claimed amount an adjustment belongs to. Details + recommendations: overnight-log D-N1/D-N3/D-N7; reviews `docs/reports/notes/s8-financial-review-1.md`, PR #15 comment 6841512829.
- **`s9-recovery` 0d8021b:** S9 complete, three review rounds clean, full 2222 pass, negctl OK. Stacked on S8; merge after S8.
- **Blocked:** `s11-perf` S11.2 (local Docker VM, 7.65 GiB, dies at the 1M import confirm step; D-N9). Only change: perf test pins clock to 2026-07-10 (not executed).
- **Antigravity:** `ui/qa-1` Playwright 8/18 fail → `antigravity-prompt-qa-1-fixes.md`. `ui/s7` waits for #15. S7–S10 UI briefs not written (write each after its backend merges).
- **Other owner items:** D-N2 unpaid-case deadline basis, D-N4 retention/deletion design (5 questions in `k1-s11-audit-seal.md`), D-N5 receipts after write-off, D-N6 S10 figure definitions (`m1-s10-reporting.md`), D-N8 ZAP/pen test/ESLint plugin/Dependabot.

## Next steps, in order
1. If CI runs again: merge #20, #21, #22 one at a time (6/6 green + review recorded on the PR); `git pull` main after each; re-chain migrations if two heads appear.
2. After the owner decides D-N1/D-N3/D-N7: align BUSINESS_LOGIC + code + tests on the chosen rule (expected values from ground truth / hand derivation), independent financial review on the change, then merge s7-adjustments → s7-claims → PR #15 → S8 PR → S9 PR → S10 PR, one at a time, full suite + negctl per merge. Each later branch merges main in and re-chains its migration first.
3. After each backend merge: UI brief + Antigravity prompt (S7 adjustments queue, S8 workbench, S9 queue, S10 dashboard) in `docs/reports/notes/`; verify Antigravity branches yourself (lint, typecheck, vitest, Playwright ≤4 specs per batch, clear rate_limit_counters, stop veyra-recover-frontend-1 while vite runs from a worktree).
4. Remaining S11: S11.2 perf (needs more Docker memory), S11.4 a11y, S11.5 deploy (hosting = owner), S11.8 pilot review. Then `docs/reports/RELEASE_1_REPORT.md`.
5. Housekeeping: `s11-docs-drill/backups/` holds a gitignored synthetic dump with password hashes; delete only if the owner agrees.

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
  - At most 3 at once.
  - Use the cheaper model (sonnet) for builders and opus for reviewers.
  - Ponytail rules apply: shortest working diff, reuse before writing, no new dependencies or abstractions. They never relax quality.
- **Decisions:**
  - Rule-4 owner decisions: skip and log them, then continue.
  - **HARD STOP** on any financial discrepancy between ground truth, BUSINESS_LOGIC and code: report it to the owner and do not choose.
  - Never pass `--no-verify`, never force-push, never rewrite history.
- **No free model:** OmniRoute/OpenRouter has been removed. Use Claude only.
