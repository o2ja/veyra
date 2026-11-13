# Resume prompt (2026-11-12 night stop): paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Resume the Veyra Recover build. The deadline is 2026-11-15.

Read ONLY these: this file, docs/reports/STATUS_2026-11-12.md, docs/PROJECT_STATUS.md §3, and your memory. The per-branch resume notes are `docs/reports/notes/<branch>-resume.md`, each on its own branch. Read one only when you work on that branch.

First run `git worktree list`, `git status`, `gh pr list` and `git fetch`. Commit and push any leftovers.

## Update 2026-11-13 (overnight run) — read this first
State and decisions: `docs/reports/notes/overnight-log.md` MORNING SUMMARY (D-N0 CI billing, D-N1/D-N3 financial hard stops, D-N2, D-N4).
- Merged: #18. Ready, waiting for CI: PR #20 (s11-docs-drill), PR #21 (s11-audit-seal), PR #22 (s11-security). Merge each only with 6/6 green; re-chain migrations at merge (#21 adds 20261115_0900_audit_seals on calc_runs).
- s11-perf: S11.2 blocked (local Docker VM dies on the 1M import; D-N9).
- Held by D-N7: s10-reporting c9f971f (stacked on S9).
- Held by D-N1/D-N3: s7-adjustments 17a70c9 → s7-claims/#15; s8-reconciliation edaa230 (re-chained, 1 head).
- s9-recovery (stacked on S8): 2nd-pass serious N1 + negctl fixes by J1; then a 2nd-pass check of that diff. s10-reporting (stacked on S9): S10 core backend by M1.
- Agents die with the session: relaunch from the branch resume notes (`<branch>-resume.md`, `docs/reports/notes/<id>-*.md`).

## Next steps, in order
1. **PR #18 (s11-ops, 9c7ba2c):** it is ready, with three security passes and CI green. Merge it if the owner has allowed `gh pr merge`; otherwise ask the owner to merge it.
2. **s7-adjustments (worktree .claude/worktrees/agent-adc7160f14611b131):** finish the WIP fixes for the 2nd financial review (B1 double deduction, S1 DB triggers, M1–M4; see the resume note). Run ONE full suite with REBATE_AI_WRITER_PASSWORD set, plus isolation, the RLS-off control (negctl), ruff, mypy and lint-imports. Fix the mypy error in tests/integration/test_end_to_end.py:17. Then run an independent financial pass on the fix diff, merge s7-adjustments into s7-claims, and merge PR #15 when CI is green.
3. **s8-reconciliation (worktree .claude/worktrees/i1-s8):**
   - Merge origin/s7-adjustments in.
   - **Re-chain** the migration: `20261117_0900_remittances.down_revision = 20261117_0900_neg_claims_guard`, then check that `alembic heads` = 1.
   - Finish S8.4–S8.7 per brief I1 and its resume note.
   - Reviews: financial, then security + DB.
   - Open the PR and merge it.
4. **s9-recovery (worktree .claude/worktrees/j1-s9):**
   - Merge origin/s8-reconciliation in after its re-chain.
   - Finish Phase B per brief J1 and its resume note.
   - Reviews: security + DB, and financial on any recovered or written-off amounts.
   - Open the PR and merge it after S8.
5. **Antigravity:**
   - After ui/s7 is reported, read ui-report-s7.md and the diff. Run lint, typecheck, vitest, and Playwright in batches of at most 4 specs (clear rate_limit_counters between them; stop veyra-recover-frontend-1 while running vite from a worktree and restart it afterwards). Get a reviewer pass, then merge after #15.
   - Its next task is ready: docs/reports/notes/antigravity-prompt-qa-1.md.
   - Write UI briefs for the S7 adjustments queue, S8 workbench and S9 queue as each backend merges.
6. **S10** (summary job, dashboard and drill-down, tier tracker, trace resolver, audit pack), then the rest of **S11** per IMPLEMENTATION_PLAN. Core first, extras second.

## Owner decisions pending (see STATUS)
OD-3/OD-4 production object storage; the merge permission. Do not block on them: log them and continue.

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
  - CI green before any merge.
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
