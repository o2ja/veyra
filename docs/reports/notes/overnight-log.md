# Overnight log (2026-11-11/12, unattended)

## MORNING SUMMARY
1. Completed and merged: S4 backend (#6, 99dc074) — before tonight.
2. In progress: S4 UI on `ui/s4` (Antigravity; WIP 85b3a19, draft editor unfinished). S6 backend on `s6-backend` and S5 runs on `s5-runs` (builder agents, parallel).
3. Blocked / needs decision: none yet.
4. Failures/findings: main CI c0dfeae backend job failed on flaky `test_register_timing` (34% > 25%, docs-only commit; same code green at 99dc074) → rerun.
5. Resume: see PROJECT_STATUS.md §3.

## Log
- Step 0: ui/s4 WIP committed+pushed (85b3a19). No other worktree had uncommitted work. S4 merged on main (99dc074, CI green); c0dfeae timing flake rerun.
- Decision: S6 and S5 built by two builder agents (cheaper model) in parallel in their worktrees, per owner usage rules; orchestrator keeps reviews (security, DB, financial) and all merges. Migration chain fixed up front: S6 `20261113_0900_extraction` → S5 `20261114_0900_calc_runs` (re-chained at merge).
- Decision (interpretation of owner's claim-window rule): `month_end` = SPA basis (old SPA `period_end` renamed), `period_end` = rebates only (measurement period end + days); SPA with `period_end` rejected. Engine edit delegated to S5 builder under financial review.
- 17:33 main CI 99acc6a: e2e job failed in Playwright apt install (runner network, exit 100), backend green → rerun 43037664932.
- S4 UI (Antigravity finished; its report says e2e NOT RUN in its sandbox). My check on ui/s4: lint, typecheck, 18 unit tests pass. Playwright agreements spec against the real stack: failed on axe (label/select-name: shared `Field`/`SelectField` let a caller's `id` break the label's htmlFor — fixed at the root), duplicate Validate locator, and W7 amending `name`, which exposed a backend gap: the name is stored per agreement, so an amended name was silently dropped → validator now rejects it (`mismatch`), PR #9. After fixes: agreements spec PASS; full Playwright 5/6 then W1-W2 PASS alone (the failure was the dev DB registration rate limit across specs, cleared in dev only). ui/s4 at 5a22c19. Pending: independent UI review (waits for a free agent slot), then merge.
- Dev stack: worker container was stale (pre-S4 code; `agreements_expiry_sweep` dead-lettered) → restarted. Gap logged: the sweep reschedules itself from inside the job, so one dead-lettered run stops that tenant's daily sweeps (follow-up: re-seed on startup/settings read).
- S6 (PR #8) built; security+DB review FAIL (4 serious: audit atomicity on retry, stuck active rows after dead-letter, missing rate/concurrency limits per AI_PROCESSING §175, api-contract/doc) + 8 minor → fixes sent back to the builder.
- 5-hour usage limit hit (all agents stopped ~17:50); resumed after reset. S5 WIP committed+pushed (d743763). S6 WIP not committable (truncated test file fails the hook; not bypassed) → builder resumed to finish it.
- PR #9 CI: e2e failed 3/3 retries (runner cannot resolve Ubuntu mirrors — GitHub/Azure infra), backend failed on `test_login_timing` flake (34%, reversed direction vs earlier flake). Both environmental; rerun later. If the timing test keeps flaking, consider a follow-up to make it robust (more samples) — not weakened tonight.
