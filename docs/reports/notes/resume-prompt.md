# Resume prompt (2026-11-12, usage limit) — paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Good morning/afternoon. Resume the 3-day plan (deadline 2026-11-15). Read ONLY: this file, PROJECT_STATUS.md §3,
docs/reports/notes/overnight-log.md MORNING SUMMARY, and your memory. Same rules as the original instruction
(core first, one review per stage, financial review on money, CI green before merge, push often, no questions except
rule-4 or financial discrepancy, no narration). Check `git worktree list`, `git status`, `gh pr list`; commit+push leftovers.

State at cut-off:
- main has #10–#14 merged (D1/D2/D3 done; S3 gaps + end-to-end integration; perf baselines).
- PR #15 `s7-claims` (S7.1–S7.3, CI green): an opus review (security+DB+money) was running. Re-run ONE review if no
  result is recorded in the PR/notes, fix findings, merge. Then write the S7 UI brief + Antigravity prompt
  (missed claims, batch builder/detail, export templates, submit dialog; adjustments queue only after F2 merges),
  print it, say "ANTIGRAVITY READY".
- PR #16 `ui/s5-s6` (S5/S6 UI by Antigravity, batch 2 fixes; lint/typecheck/vitest 30 pass): second-pass review
  was running + CI e2e. If CI green and re-review (items 1–17 of ui-brief-s5-s6-fixes.md) PASS → merge; then write
  the S3–S6 batch report in docs/reports/ (5-line decisions summary first; include: narrow-run SPA-signal follow-up,
  perf eligible=0 caveat, S4 minors fixed, endpoint gaps download-url/boxes).
- Agents that were running (worktrees; briefs in docs/reports/notes/briefs/): F2 `s7-adjustments` (stacked on
  s7-claims; needs financial review after), G1 `s8-matching` (pure package), H1 `s11-ops`. They died with the session:
  check each branch on origin and its notes file; relaunch each from its brief + "continue from the branch state".
- Then continue: S8 (remittance ingestion CSV/XLSX → PDF via extraction review with recorded fixtures; orchestration,
  workbench API; Antigravity UI) and S9 (cases, deadlines, queue; notifications, digest) = day 2; S10 + remaining S11
  code (audit seal, ZAP baseline, a11y pass, QA browser pass, release report) = day 3.
- Local e2e: register limit 5/h/IP → clear `docker exec veyra-recover-postgres-1 psql -U postgres -d rebate -qc "delete from rate_limit_counters"` between ≤4-spec batches; stop veyra-recover-frontend-1 when running vite from a worktree, restart it after.
