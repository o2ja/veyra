# C1 — S5 calculation runs: orchestration, schema, APIs (BACKEND)

Agent id: `c1-s5-runs`. Branch: `s5-runs`. Test DB: `rebate_test_c1`. You MAY run the isolation suite and its
negative control (only DB agent using `rebate_negctl` this wave; C2 must not).

## Read
`COMMON.md`; `CLAUDE.md` rules section; `IMPLEMENTATION_PLAN.md` §S5 (S5.3–S5.5, S5.7; acceptance);
`BUSINESS_LOGIC.md` §4–§9, §12–§13 (targeted); `DATABASE_SCHEMA.md` §3.7; `API_SPECIFICATION.md` (runs, results,
accruals, tracker, preview-calculation); `TESTING_STRATEGY.md` §3; `TEST_DATA_SPEC.md` §3;
`docs/reports/notes/a2-engine.md` (engine interpretations C6–C16 and interfaces). Ground truth:
`testdata/generated/ground_truth/expected_calculation.json`, `expected_rebates.json`, `expected_rebates_nwnd.json`,
`expected_volume_summary.json`.
Reuse: `rebate_engine` (pure, on main — do NOT edit; report mismatches), `modules/masterdata`, `modules/ingestion`
(sales/purchase lines), `modules/agreements` (versions as-of), `core/jobs.py`, isolation suite.

## Build — module `modules/claims/{runs,loaders,persistence}.py` (+ service, router, schemas, jobs)
- S5.4 migrations: `calculation_runs`, `calculation_results`, `rebate_accrual_entries`, `claim_adjustments`;
  append-only triggers; RLS ENABLE+FORCE via the migration helper. Start after the current alembic head on main.
- S5.3 loaders (lines, agreement versions active as-of, prior consumption excluding lines in the run, prior
  claims interface, aliases/parent chain, return→original links), per-tenant advisory lock, chunked persistence,
  run status machine, totals + checksums, partial runs invisible to readers, SPA before rebate, SPA-line
  identification per §5.4 (OR rule, already in the engine). Recalculation proposals: compare against an
  interface (`FrozenClaimSource`) that returns nothing until S7 builds `claim_lines`; real code, documented, tested
  with fixture rows built in the test.
- S5.5 APIs: runs (create, status, list), results (+filters, keyset), result with trace, accrual ledger, tracker
  data, `POST /agreement-drafts/{id}/preview-calculation` (runs the engine on the draft payload, persists nothing).
- Permissions per route (tenancy catalog), audit on mutations, isolation suite + negative control extended to
  every new table and route.

## Tests (S5.7)
End to end from imported CSV (real import pipeline, real files): every scenario/variant in
`expected_calculation.json`; `expected_rebates*.json` and volume expectations; idempotent reruns produce
byte-identical results/trace hashes; partial-run invisibility (failure mid-run); concurrent run rejected by the
lock; no-float lint over `modules/claims`; performance baseline 1M lines (`@pytest.mark.performance`, deselected;
rows/s + memory in notes). Engine errors for `cap_scope=per_agreement` / `tier_metric=quantity` surface as a clear
run/preview error (problem response), not a 500.

## You own
`backend/src/rebate/modules/claims/**`, new migration files, `backend/tests/{integration,isolation}/` additions for
claims, `docs/reports/notes/c1-s5-runs.md`. Router/worker registration lines in app/worker entry points and the
permission catalog entries are allowed (list them in notes).
