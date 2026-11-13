# s9-recovery resume (J1) — 2026-11-12 stop
Pushed: f9a1575 phase A pure domain (62 tests); 805b68f + c5227b0 S9.1 migration 20261118_0900_recovery_cases (down 20261117_0910_recon_matches), case service, S9.4 jobs, API, 13 integration tests. This WIP = test_recovery_cases.py edits, UNVERIFIED.
Accepted decisions: only the most urgent reached reminder; deadline day reminds; recovered when total >= case amount.
Left: verify S9.4 jobs (deadline_sweep, notification dedupe, digest via existing mail abstraction — STOP if none), four-eyes + state machine API tests, isolation per route, OpenAPI/spec/client regen, re-merge origin/s8-reconciliation (after I1's re-chain) and re-check alembic head, full suite/isolation/negctl/mypy/ruff/lint-imports, notes j1-s9-recovery.md. Then security + DB review, PR after S8 merges.

## Update 2026-11-13 (overnight run)
Done: jobs verified + tests (test_recovery_jobs.py, 6), four-eyes/state machine/idempotent write-off tests, isolation for tables and routes, OpenAPI/spec/client regenerated, audit key bug fixed, notes in j1-s9-recovery.md.
Left: re-merge origin/s8-reconciliation (regenerate client, check alembic head, importlinter `reconciliation` contract and calc_support `FixtureClaims` are S8-side breakages), negctl, one more full run after the merge, then security + DB review, PR after S8 merges.
