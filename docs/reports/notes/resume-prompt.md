# Resume prompt (2026-11-14 early): paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Resume the Veyra Recover build. The deadline is 2026-11-15.

Read ONLY these: this file, the SUMMARY at the top of `docs/reports/notes/overnight-log.md`, `docs/PROJECT_STATUS.md` §3, and your memory. Per-branch notes (`docs/reports/notes/<branch>-resume.md`, `<id>-*.md`) live on their branches; read one only when you work on that branch.

First run `git worktree list`, `git status`, `gh pr list` and `git fetch`. Commit and push any leftovers. Start the dev stack if it is down: `docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2 up -d` (from main).

## State (2026-11-14 early)
- **Main:** 19b26c4. Merge gate = `make ci-local` 6/6 (CLAUDE.md §19). `gh pr merge` is blocked by the auto-mode classifier → the owner merges. Plan: `docs/reports/notes/FINISH_PLAN.md`.
- **Machine:** C: (temp/Docker) nearly full (3 GB free) and RAM tight: Docker crashed once (restarted). Run at most a gate + 1 agent; one test container at a time.
- **#21 S11.1** `s11-audit-seal` c35c2e5 (main merged): gate re-run was interrupted by the session end → merge latest main, re-run ci-local, post, owner merges. (Earlier 6/6 at 4f95e43 is stale: main moved.)
- **#22 S11.3** a7f7f6a reviewed; gate after #21. **#24** D-N8 draft.
- **S7** `s7-claims` e43aefd: 5th financial review MERGEABLE. PR #15: merge main, gate.
- **S8** `s8-reconciliation` 40dc42f: reviews 3–5 done (`s8-financial-review-{3,4,5}.md`), MERGEABLE (minors fixed + verified). D-N11 interim open (owner).
- **S9** `s9-recovery` 1983f5a: final S8 merged (single head), D-N2 + fifth case (CL-4 short_paid 50.00, due 2026-09-29) reviewed MERGEABLE WITH MINORS → fixed (real-flow reclaim test). Review note `s9-review-final-s8.md`.
- **S10** `s10-reporting` 7054beb: S9 merged; reporting tests 16 pass at ccb103c (expectations per D-N3: 6 collected rows, CL-4 paid 200.00). Unverified after the last S9 merge (disk full): re-run test_reporting*.py + alembic heads, then the full S10 review (money + security + DB).
- **UI (all built, none verified by Playwright yet — external agents cannot reach localhost):** `ui/s7` ea0b3e2, `ui/s7-adjustments` dccc040 (review 1 fixed), `ui/s8` 063e0c4, `ui/s9` 9f8a39d (OpenCode), `ui/s10` 0084ee0, `ui/qa-1` 423d306. Each: merge main in after its backend lands, reviewer, Playwright (JOBS=e2e ci-local script), merge.
- **Other owner items:** D-N4 retention design, D-N8 ZAP/pen test, D-N9 Docker memory (S11.2), D-N11 (S8 H2).

## Next steps, in order
1. Free space on C: if possible (owner). Re-run #21's gate (merge main first); owner merges.
2. S10: re-run test_reporting*.py + heads; full S10 review (opus) → fixes.
3. Reviews of ui/s8, ui/s9, ui/s10 (opus, one at a time) → fixes by a Claude UI fixer.
4. Merge chain, one gate each: #15 S7 (+ui/s7, ui/s7-adjustments) → S8 (+ui/s8) → S9 (+ui/s9) → S10 (+ui/s10) → #22 → ui/qa-1. Playwright for each UI branch inside its gate.
5. S11.4 a11y pass (builder), RELEASE_1_REPORT.md. Owner: S11.5 deploy, S11.8 pilot, D-N items.

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
