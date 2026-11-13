# Resume prompt (2026-11-14): paste into a new session started in D:\veyra\rebate-recovery-docs\rebate-recovery

Resume the Veyra Recover build. The deadline is 2026-11-15.

Read ONLY these: this file, the SUMMARY at the top of `docs/reports/notes/overnight-log.md`, `docs/PROJECT_STATUS.md` §3, and your memory. Per-branch notes (`docs/reports/notes/<branch>-resume.md`, `<id>-*.md`) live on their branches; read one only when you work on that branch.

First run `git worktree list`, `git status`, `gh pr list` and `git fetch`. Commit and push any leftovers. Start the dev stack if it is down: `docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2 up -d` (from main).

## State (2026-11-14, ~82% usage)
- **Main:** this commit. Gate = `make ci-local` 6/6 (CLAUDE.md §19); `gh pr merge` blocked by the auto-mode classifier → owner merges. Plan: `docs/reports/notes/FINISH_PLAN.md`.
- **Machine:** C: freed (30 GB). RAM tight: run a gate + at most 1 agent; one test container at a time.
- **#21 S11.1** `s11-audit-seal` (head = enrolMfa e2e fix): needs main merged in + a full gate. Two e2e flake root causes are fixed on this branch: Vite reload on junit write (4f95e43) and `enrolMfa` returning before the post-MFA `navigate("/")` (wait added in e2e/support.ts). The 06:00 gate was stopped for the docs push.
- **#22** a7f7f6a, **#24** draft: after #21.
- **S7** `s7-claims` e43aefd: final (5th financial review MERGEABLE). PR #15: merge main, gate.
- **S8** `s8-reconciliation` 40dc42f: final (reviews 3–5). D-N11 owner question open (see below).
- **S9** `s9-recovery` 1983f5a: final S8 merged, reviewed, minors fixed.
- **S10** `s10-reporting` 79614c0: full review MERGEABLE WITH MINORS (`s10-review-full.md`) → minors fixed (audit pack is now **POST**; AUDIT_PACK_REQUESTED audited; property test widened). Targeted set not re-run after the property-test fix → the gate covers it.
- **UI:** `ui/s7` ea0b3e2, `ui/s7-adjustments` dccc040 (review fixed), `ui/s8` (final S8 merged in; review 1 NOT MERGEABLE → `docs/reports/notes/ui-review-s8-1.md` on ui/s8: send all items to a Claude UI fixer; its job-polling bug (statuses queued|running|succeeded|failed) also exists in claims/claim-batch-detail-page.tsx:215 on ui/s7 + ui/s7-adjustments — fix there too), `ui/s9` 9f8a39d (OpenCode, not reviewed), `ui/s10` 0084ee0 (not reviewed; must switch the audit pack call to POST), `ui/qa-1` 423d306. None Playwright-verified.
- **Preview for the owner:** api/worker/frontend run from `D:/veyra/veyra-ui-s10` on database `rebate_preview` via the override `preview.yml` (session scratchpad; content: api + worker `DATABASE_URL` → `.../rebate_preview`). The dev DB `rebate` is untouched (still at #21's head). Before the next gate run `make up` in main (ci-local recreates the containers anyway).
- **Owner items:** D-N11 (S8 H2: under the interim rule a later payment of B2 shows INV-10020 short 50.00 next to CL-4's 50.00 — the same 50.00 twice; decide how a cross-batch adjustment item is matched), D-N4, D-N8, D-N9.

## Next steps, in order
1. #21: merge main, full gate, post, owner merges.
2. Reviews (opus, one at a time): ui/s8 (if not done), ui/s9, ui/s10 → Claude UI fixer per review (ui/s10 also switches audit pack to POST).
3. Merge chain, one gate each: #15 S7 (+ui/s7, ui/s7-adjustments) → S8 (+ui/s8) → S9 (+ui/s9) → S10 (+ui/s10) → #22 → ui/qa-1. Merge main into each first; Playwright runs inside the gate.
4. S11.4 a11y pass, RELEASE_1_REPORT.md. Owner: S11.5 deploy, S11.8 pilot, D-N items.

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
