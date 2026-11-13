# P1 S11.2 performance run (2026-11-13) - BLOCKED, no new numbers

## What ran
- Generated the 1M file with the existing generator (`testdata/generate.py --volume 1000000`, 24 s, 163 MB, outside the repo).
  Ground truth for that file: 930,149 eligible lines, 489,788,511.50 total, 69,851 exception lines (seed 20261109, run_date 2026-07-10).
- Started the existing harness (`tests/integration/test_calculation_performance.py`, `REBATE_TEST_DB=rebate_test_p1`).
  About 3 minutes in (import at the mapping step) Docker Desktop itself died: `error waiting for container: unexpected EOF`,
  then `Docker Desktop is unable to start`; the `docker-desktop` WSL distro stayed Stopped for 15+ minutes.
  Not a Postgres crash as such, but the same class of host failure. Per the brief I did not retry, and I did not restart
  Docker (shared by other agents). No timings, no EXPLAIN plans, no 5M run were produced.

## Change made (eligible=0 caveat, untested)
- The perf test now pins the fake clock to 2026-07-10 (the generator's ground-truth run date) right before the run, so the
  June 2026 invoices are inside the SPA claim window and lines can be eligible. Ground truth is unchanged (generator untouched).
  Not executed (Docker down); ruff/mypy not run for the same reason. A re-run should compare `eligible` with 930,149.
- No index, no migration: nothing measured, so nothing changed.

## To resume
Start Docker Desktop, then run the command in `docs/reports/PERFORMANCE_BASELINES.md` (Method) with `REBATE_TEST_DB=rebate_test_p1`
and `PERF_SALES_CSV` pointing at a freshly generated 1M file; then EXPLAIN the hot queries on that DB and drop it.
Choice recorded: 5M not attempted (import alone would be roughly 2 h and ~10 GB of Postgres data on a 7.6 GiB VM that just crashed).
