# E3 — performance baselines + S3–S6 doc catch-up (branch `e3-perf-docs`, test db `rebate_test_e3`)
Read COMMON.md rules/environment, then ONLY: CLAUDE.md, TESTING_STRATEGY.md (performance part),
`tests/performance/*`, `testdata/generate.py --help`, the existing perf result in PROJECT_STATUS.md §3, and the
four docs below plus `git log --stat 99dc074..main` (what changed in S3–S6; read the notes in
`docs/reports/notes/{a1,b1,c1,c2}*.md` for doc deltas).

Do:
1. 1M-row sales file via `testdata/generate.py --out <git-ignored dir>` (never commit it). Measure, on the shared
   stack in your own database: import time + peak memory (API/worker), calculation-run time + peak memory for a
   run over the whole period. One attempt each; if Postgres crashes, record it and stop that measurement.
2. EXPLAIN (ANALYZE, BUFFERS) for the 3 slowest queries you observe (pg_stat_statements if available, else the
   run's logged timings). Add an index only where a plan shows it helps and you measured before/after; as a
   migration (revision id ≤ 32 chars, chained on main's head).
3. Record results in `docs/reports/PERFORMANCE_BASELINES.md` (hardware note, commands, numbers, plans summary).
4. Brief doc updates for what changed in S3–S6 (behaviour that actually changed only, short):
   ARCHITECTURE.md, API_SPECIFICATION.md, DATABASE_SCHEMA.md, PROJECT_STATUS.md (§1/§2/§4 only; §3 is the
   orchestrator's). BUSINESS_LOGIC.md is NOT yours.
Own only those docs, the perf report, perf tests/scripts, and any index migration.
