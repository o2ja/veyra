# s9-recovery resume (J1) — 2026-11-12 stop
Pushed: f9a1575 phase A pure domain (62 tests); 805b68f + c5227b0 S9.1 migration 20261118_0900_recovery_cases (down 20261117_0910_recon_matches), case service, S9.4 jobs, API, 13 integration tests. This WIP = test_recovery_cases.py edits, UNVERIFIED.
Accepted decisions: only the most urgent reached reminder; deadline day reminds; recovered when total >= case amount.
Left: verify S9.4 jobs (deadline_sweep, notification dedupe, digest via existing mail abstraction — STOP if none), four-eyes + state machine API tests, isolation per route, OpenAPI/spec/client regen, re-merge origin/s8-reconciliation (after I1's re-chain) and re-check alembic head, full suite/isolation/negctl/mypy/ruff/lint-imports, notes j1-s9-recovery.md. Then security + DB review, PR after S8 merges.

## Update 2026-11-13 (overnight run)
Done: jobs verified + tests (test_recovery_jobs.py, 6), four-eyes/state machine/idempotent write-off tests, isolation for tables and routes, OpenAPI/spec/client regenerated, audit key bug fixed, notes in j1-s9-recovery.md.
Left: re-merge origin/s8-reconciliation (regenerate client, check alembic head, importlinter `reconciliation` contract and calc_support `FixtureClaims` are S8-side breakages), negctl, one more full run after the merge, then security + DB review, PR after S8 merges.

## Update after review 1 (commit 7fe11bb, merged origin/s8-reconciliation)
Fixed: S1 hook via reconciliation.on_finalise (+ CM-7781 finalise -> GET /cases test; repeated unpaid is skipped by ON CONFLICT DO NOTHING, tested on open_cases directly, not via two real finalisations), S2 write-off retires the reclaim / 409 reclaim_included, S3 audit allow-list reason + write_off_reason, S4 active members only, M1 DB CHECK, M2 proposal voided on recovery/status change, M3 real days in title, M4 on_dead schedules next sweep (tested by direct call), M5 ETag on replay / written_off_amount / Money, M6 tests. DATABASE_SCHEMA 3.10 and API spec 2.8 updated.
Runs: full suite no ignore 2217 passed, 0 failed (rebate_test_j1, includes isolation); ruff check/format, mypy (300 files), lint-imports (16 kept) clean; one alembic head 20261118_0900_recovery_cases.
Not done: negctl (orchestrator), npm test, SMTP-failure retry test, M7 (owner: unpaid case with remittance_date basis has no deadline).

## Update after negctl + review 2
negctl restructured and OK (211 detected, independent passed); N1 (write-off after a submitted reclaim, 1,080.00 test), N2 (write_off_reason stored with four-eyes off), N3 (2-decimal titles) done. Targeted recovery + isolation + audit + domain tests: 536 passed; ruff, mypy, lint-imports clean. Full suite not re-run after N1-N3 (last full run: 2217 passed at 7fe11bb). Flag for owner: S9 rejects S7 reclaim adjustments via reject().
