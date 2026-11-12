# Resume prompt (2026-11-12 evening, usage limit) — paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Resume the 3-day plan (deadline 2026-11-15). Read ONLY: this file, PROJECT_STATUS.md §3, and your memory. Same rules
(core first, one review per stage, financial review on money, CI green before merge, push often, ask only rule-4 or
financial discrepancy, no narration). Check `git worktree list`, `git status`, `gh pr list`; commit+push leftovers.

State at cut-off:
- main c1dc8f0: #16 S5/S6 UI and #17 S8 matching merged; S3–S6 batch report `docs/reports/S3_S6_BATCH_REPORT.md`
  (reviewed, fixes applied 857312a). Full suite on main 1630 passed.
- PR #15 `s7-claims` (S7 core): review recorded on the PR; serious fixed 39a1c12; MERGE GATED on `s7-adjustments` (F2).
- `s7-adjustments` (F2, worktree agent-adc7160f14611b131): built (1794 passed); financial review: no discrepancy,
  5 serious → fix list sent to F2 (stale-run propose, return-reversal de-dup on version chain, caps count all
  non-released claim lines §4.2, 409 already_claimed across versions, propose lock, API spec §2.8, insert guard,
  re-bind test, §9.3 netting doc). If F2 died: relaunch from brief F2 + "apply the financial-review fix list in the
  PR #15 / notes" (list above), then 2nd-pass financial review on the fix diff, then merge s7-adjustments into
  s7-claims and merge #15.
- RESOLVED (owner 2026-11-12, see overnight-log MORNING SUMMARY):  `cannot_be_net_claimed` adjustments — BUSINESS_LOGIC §7 says held as
  `proposed`; ground truth `claim_batches_fixture.json` includes ADJ-2026-0001 in the VOLT batch (no
  allow_negative_claims). Recommendation: follow ground truth only if the owner confirms; otherwise block inclusion
  and negative batch totals.
- Other owner items (in S3–S6 report): S5 sign-off, OD-2 + quality bar, split-period tiers R2, R2 validator refusals,
  OD-1; S8 matching: paid=0 without rejection code (recommend short_paid / short_paid_unexplained).
- PR #18 `s11-ops`: CI green (files.spec upload flake once, rerun green); security review was running — re-run ONE
  security review if no result on the PR, fix, merge.
- S7 UI: Antigravity on `ui/s7` (worktree D:/veyra/veyra-ui-s7, base = s7-claims + ui/s5-s6); brief/prompt there.
  When done: read ui-report-s7.md + diff, lint/typecheck/vitest, e2e in ≤4-spec batches (clear rate_limit_counters),
  reviewer, merge after #15.
- Next (day 2): S8 reconciliation backend (remittance ingestion CSV/XLSX → PDF via extraction with recorded fixtures,
  persistence of matches, workbench API) using `rebate_matching` (interfaces: docs/reports/notes/g1-s8-matching.md);
  brief not written yet (read IMPLEMENTATION_PLAN S8 section). Then S9, day 3 S10 + S11.
- Local e2e: stop veyra-recover-frontend-1 when running vite from a worktree, restart after.

## Update (96 % usage, owner decisions 2026-11-12)
- Owner decisions: (1) cannot_be_net_claimed stays proposed (§7), batches <= 0 refused, VOLT allow_negative_claims=true
  in master_manufacturers.json + test with a manufacturer without it → sent to F2 (s7-adjustments). (2) paid=0 without
  rejection code = short_paid / short_paid_unexplained → PR #19 (s8-unpaid-rule, §10.3 + test; merge when CI green).
  (3) S5 signed off; OD-1 confirmed; 3 validator refusals + split-period = Release 2; OD-2 not now (recorded fixtures).
  → still to write into PROJECT_STATUS (decision table) + IMPLEMENTATION_PLAN if not done.
- PR #18 security review: 1 blocker + 4 serious (see PR comment) → relaunch H1 from brief with that list; SeaweedFS in
  prod is an owner decision (OD-3/OD-4) — keep as flagged, least-privilege + Caddy method allow-list meanwhile.
