# Performance baselines (1M lines)

Measured 2026-11-12 on `main` after S3-S6 (branch `e3-perf-docs`). Targets: `TESTING_STRATEGY.md` section 10 (NFR-04). Synthetic data only.

## Environment

Shared Docker dev stack on a Windows host: Docker VM 7.6 GiB RAM, 12 CPUs, Postgres 16 container (`shared_buffers` 128 MB, `max_connections` 100), SeaweedFS and ClamAV running. Other agents were using the same stack during the run, so the numbers are conservative and noisy (single attempt, no repetition). This is not the reference VPS size; the reference-VPS run is still open.

## Method

```sh
# file (git-ignored dir, never committed): 1,000,000 rows, 163 MB
python testdata/generate.py --volume 1000000 --out <scratch>      # needs openpyxl + reportlab (throwaway venv)
# one run: real import pipeline (file object seeded directly, as the 50 MB upload cap is not lifted), then one
# calculation run over the whole period for the activated SPA-VOLT-2026-884 agreement
docker compose ... run --rm --no-deps -T -v <scratch>/sales:/perf:ro -e PERF_SALES_CSV=/perf/sales_volume_1000000.csv \
  -e REBATE_TEST_DB=rebate_test_e3 ... api sh -c 'pytest -x -q -s --no-cov -m performance tests/integration/test_calculation_performance.py'
```

Test: `backend/tests/integration/test_calculation_performance.py` (prints the import phase and the run phase). Peak RSS is the test process, which runs API and worker in-process. Postgres container memory sampled with `docker stats` every 20 s.

## Results (one run, 1 passed in 2141 s)

| Measurement | Now (2026-11-12) | Earlier baseline | Target |
|---|---|---|---|
| Import 1M rows (parse, validate, stage, load) | **1371.6 s** (22.9 min, 729 rows/s) | 1076.6 s (929 rows/s), S3 notes 2026-11-10 | < 30 min |
| Calculation run, 1M lines, whole period | **761.7 s** (12.7 min, 1,313 lines/s) | 630.5 s (1,586 lines/s), S5 notes (2,000-line file x 500) | < 15 min |
| Peak RSS, test process (API + worker) | **1,234 MB** after import; unchanged by the run (children 119 MB) | 1,184 MB import / 1,253 MB run | n/a |
| Postgres container peak memory | 2.92 GiB (sampled) | 1.65 GiB (import only) | n/a |
| Postgres crash | none | none (after earlier failed attempts) | n/a |

Both phases are within the NFR-04 targets on this VM, but 27 percent (import) and 21 percent (calculation) slower than the earlier baselines. The tests ran under a shared, loaded Docker VM both times and no code path was timed in isolation, so the difference is not attributed to a cause; the S4-S6 changes (agreement/extraction tables, denormalised result columns) add write volume to `calculation_results` and are one candidate, load from other agents another. Not investigated further.

Stored volume afterwards: `calculation_results` 999,362 rows / 2,295 MB (about 1.6 KB per row including trace and denormalised columns), `import_staged_rows` 1,000,676 / 2,199 MB, `sales_document_lines` 997,989 / 583 MB, `sales_documents` 1,000,000 / 373 MB. The table counts are `pg_stat` estimates (`n_live_tup`), not exact; the test itself asserts that the import inserted every data row and that the run's `totals.lines` equals the data rows (it passed).

### What the volume file exercises (important)

`eligible=0`. All 1,000,000 lines were calculated but ended as exceptions: 930,149 `outside_claim_window`, 29,990 `price_mismatch`, 19,975 `ineligible_customer`, 19,886 `missing_spa_reference`. Cause: the file's invoice dates are 2026-06-01 to 2026-06-21 and the run date is the real current date (2026-11-12); the SPA claim window (month end plus the manufacturer setting) has closed for almost every line. So the engine path measured is the gate and exception path, plus tier-free SPA checks for the minority of lines that pass the window gate; the full eligible path (price-difference amount, cap tracking, trace of a payable claim) is **not** loaded, and a run with eligible lines will likely be somewhat slower per line. Rebate-tier calculation and the claw-back path were not exercised at volume. Re-measuring needs a fixed run date, which the API deliberately does not allow (the clock is wall time); the in-process test uses a fake clock fixture (`FakeClock`), so a follow-up could pin it inside the claim window (not tried).

## Query plans (EXPLAIN (ANALYZE, BUFFERS), on `rebate_test_e3` after the run)

`pg_stat_statements` is available in the image but not preloaded and cannot be enabled without restarting the shared server (not done), and the run does not log per-query timings. I therefore explained the read queries the run issues, with the loaded data (superuser session, explicit `tenant_id` filter, so RLS is not part of the plan).

| Query (code) | Plan | Time |
|---|---|---|
| Window date counts, `ingestion/calc_reads.py` (count per invoice date, 21 days, all 1M lines) | Parallel Seq Scan on `sales_document_lines` (3 workers), Hash aggregate, join to 4 import batches | 192 ms |
| One-day window read (47.6k lines of the day, `ORDER BY id`) | Parallel Seq Scan + external merge sort (3.5 MB spill per worker), nested loop to `products` | 233 ms |
| Same window with `manufacturer_id = :m` (instead of `IS NOT DISTINCT FROM`) | Bitmap Index Scan on `ix_sales_document_lines_manufacturer_date` then heap scan (30,004 buffers read) | 179 ms |
| Returned quantities (negative lines) | Index Scan on `ix_sales_document_lines_tenant_id_original_line_id` | 0.13 ms |
| Results list page (`status`, keyset by id, 50 rows) | Index Scan on pk | 1.3 ms |
| Staged-row chunk (`row_number` range of 5,000) | Index Scan on pk | 0.6 ms |
| Document natural-key lookup, 5,000 keys (loader) | Index Scan on `uq_sales_documents_natural_key` | index plan confirmed; the 1,214 ms total is dominated by my helper subquery that picks the keys (Seq Scan with OFFSET), so the lookup time itself was not isolated |

Findings:

1. The reads are not where the time goes: 21 windows x ~0.25 s is about 5 s of the 762 s run. The time is in per-line engine evaluation and trace building in Python, and in writing 1 M `calculation_results` rows of about 1.6 KB (2.3 GB incl. indexes, 6 indexes on the table). I did not profile the engine or the insert separately; that is the open next step if the run time becomes a target risk.
2. `IS NOT DISTINCT FROM` on `manufacturer_id` prevents using `ix_sales_document_lines_manufacturer_date`, so the window read seq-scans. Forcing an equality predicate gave an index plan, but 179 ms versus 233 ms (single cold-cache measurement each, data not physically ordered by date), a gain of about 0.05 s per window, about 1 s per run. That is not worth a code or index change.
3. No index was added, no migration was written: no plan showed a measurable benefit worth the cost. The import loader fix already in `main` (scalar `IN`/`ANY` instead of a row-value `IN`) holds: chunk lookups are fast.
4. Not measured: list-endpoint p95 on a 5M-line tenant, lock contention during concurrent imports/runs, the reference VPS size.

## Open / honest limits

- One run each, loaded shared VM, so the roughly 25 percent slowdown versus the earlier baselines is within plausible noise and unexplained.
- Calculation figures are for a file with no eligible lines (see above).
- Postgres memory is sampled every 20 s (peak may be higher).
- Re-run on a dedicated VPS-sized machine before pilot.


---

## 2026-11-14: 100k and 250k rows with eligible lines (S11.2, branch `perf/s11-2`)

Owner decision D-N9: no 1M run on the dev laptop; 100,000 and 250,000 rows only, one at a time, each in its own test database (`rebate_test_perf`). Docker survived both runs (no OOM, no Postgres restart). Single run each, no repetition.

**Environment.** Windows 11 laptop, Docker VM 7.649 GiB RAM (`docker stats` limit; `docker info`: 12 CPUs), Postgres 16 container (`shared_buffers` 128 MB). The dev stack (api, worker, frontend, SeaweedFS, ClamAV, mailpit, Postgres) was running but idle; this task started no other load, but other host processes were not controlled for. Not the reference VPS.

**What changed versus the 1M runs (the `eligible=0` caveat is fixed).** `backend/tests/integration/test_calculation_performance.py` now takes the scale from `PERF_ROWS` (default 100000) and pins the test clock to 2026-07-10 (the generator's `run_date`) before workspace setup, import and run. It must be before the import: the run's data cutoff and the visible agreement versions are both "now", so pinning only before the run (as the old `s11-perf` branch did) gives a run over 0 lines (I hit exactly that on the first attempt). The test asserts, from `testdata/generate.py` ground truth (`expected_volume_summary.json`, written by the generator's own replicate logic, not computed from the code under test): eligible count, per-code exception counts, and the eligible total. All matched exactly at both sizes.

Reproduce (the file is git-ignored under `testdata/generated_volume/<rows>`): `python -I testdata/generate.py --volume 250000 --out testdata/generated_volume/250000`, then the one-off `api` container with `-e PERF_ROWS=250000 -v <repo>/testdata:/testdata:ro` and `pytest -q -s --no-cov -m performance tests/integration/test_calculation_performance.py`.

### Measurements

| Measurement | 100,000 rows | 250,000 rows | Ratio (2.5x rows) |
|---|---|---|---|
| Import wall (parse, validate, stage, load) | 122.6 s (816 rows/s) | 299.4 s (835 rows/s) | 2.44x, linear |
| Calculation wall, whole period | 85.0 s (1,176 lines/s) | 184.5 s (1,355 lines/s) | 2.17x, linear |
| Eligible lines (ground truth = engine) | 93,048 | 232,720 | match |
| Exceptions (price_mismatch / ineligible_customer / missing_spa_reference) | 3,040 / 1,960 / 1,952 | 7,507 / 4,958 / 4,815 | match |
| Eligible amount (ground truth = engine) | 48,868,373.00 | 122,430,644.50 | match |
| Peak RSS during import (test process = API + worker in-process, sampled every 0.25 s from /proc) | **386 MB** | **526 MB** | +140 MB (+36%) |
| Peak RSS during calculation | 1,016 MB | 1,039 MB | +23 MB (+2%) |
| Postgres container peak memory (`docker stats`, 20 s samples) | 707 MiB | 1,267 MiB | 1.8x |
| Test container peak (`docker stats`, 20 s samples) | 977 MiB | 1,026 MiB | |
| Lines list `GET /sales-lines?limit=50` | 33 ms | 31 ms | |
| Results list `GET /calculation-runs/{id}/results?status=eligible&limit=50` | 24 ms | 27 ms | |
| Missed claims `GET /missed-claims?limit=50` | **1,468 ms** | **2,026 ms** | 1.4x |

Request times are one in-process call each, warm, as the admin principal. They are not p95.

### EXPLAIN (ANALYZE, BUFFERS) of the three list queries

The test captures the SQL the endpoint actually issues (SQLAlchemy `before_cursor_execute`) and re-runs it under EXPLAIN as superuser (RLS is not part of the plan). The test prints the full plans (`PERF EXPLAIN ...`).

| Query | 100k | 250k |
|---|---|---|
| Lines list | Backward Index Scan on `pk_sales_document_lines`, Memoize on `import_batches`; execution 0.15 ms, 11 buffers | same plan; 0.15 ms, 9 buffers |
| Results list (eligible, keyset) | Backward Index Scan on `pk_calculation_results`; 0.08 ms, 18 buffers | same plan; 0.11 ms, 17 buffers |
| Missed claims | `latest` CTE (DISTINCT ON `sales_document_line_id` over all results joined to runs and lines) sorted with an external merge (6-7 MB per worker), then a per-row NOT EXISTS SubPlan on `claim_lines`, then a top-N sort for LIMIT 50; execution **1,522 ms**, 198k shared hits + 17k reads, 2.5k temp blocks | same shape; **2,093 ms**, 475k hits + 63k reads, 6.1k temp blocks, external sorts 14-18 MB per worker |

Finding: both keyset lists are index-bounded and flat in row count. Missed claims is not: it materialises every line's latest result before applying `LIMIT 50` (`backend/src/rebate/modules/claims/missed.py`, `LATEST` and `UNCLAIMED`), so its work grows with total lines (buffers 2.4x for 2.5x rows; wall 1.4x only because of 3 parallel workers). Extrapolation (not measured): at 5M lines it would take tens of seconds, far above the NFR-04 p95 < 500 ms target; it already exceeds 500 ms at 100k. The planner also estimates 250 rows for the filtered subquery where 93,048 come out. Not changed here; candidate fix: a keyset-friendly formulation or a stored "current result per line" flag.

### Memory scaling

- **Import memory grows with row count.** 386 MB at 100k, 526 MB at 250k: about 0.93 KB per row, i.e. a floor near 290 MB plus a linear term. This is the streaming-bug signal D-N9 asked about, although mild (extrapolated, not measured: about 1.2 GB at 1M, which matches the 1,184 MB peak of the earlier 1M run). Probable cause, from reading the code only (not profiled): `SalesValidator` in `backend/src/rebate/modules/ingestion/validators.py` keeps per-row state for the whole file: `self.seen` (natural key string to a (row, 64-char hash) tuple, set at line 379) and `self.file_keys` (a tuple per invoice line, line 494), plus similar dicts for credit-note originals and customer parents. They back in-file duplicate detection, so they are inherent to the current design; they could be replaced by a DB-side duplicate check over `import_staged_rows` or a compact digest set. No refactor done.
- **Calculation memory is flat.** About 1.0 GB at both sizes (+2 percent for 2.5x rows). The test process does not release the import's memory before the run, so the figure is "after import plus run".
- Postgres container memory grows with data (707 MiB to 1,267 MiB), as expected for cache, larger sorts and indexes; not a streaming issue.

### NFR-04 comparison (extrapolation, clearly not measured at 1M)

| Target | Measured here | Linear extrapolation to 1M | Verdict |
|---|---|---|---|
| 1M import < 30 min | 250k in 299 s (835 rows/s) | about 20 min (1,198 s) | Within target; consistent with the 22.9 min measured on 2026-11-12 |
| 1M calculation < 15 min | 250k in 184.5 s (1,355 lines/s), 93 percent of lines eligible | about 12.3 min (738 s) | Within target. First figure with the eligible path loaded; per line it is not slower than the `eligible=0` run (1,313 lines/s), so the earlier worry that the eligible path would be slower is not borne out at this size |
| List endpoints p95 < 500 ms on 5M lines | lines list and results list about 30 ms, flat | not extrapolated (index-bounded) | Lines and results lists plausible; **missed claims fails (1.5 s at 100k, 2.1 s at 250k)** |

### Unverified

- No 1M run (by decision): every 1M number above is a straight-line extrapolation from 100k and 250k, and import or calculation could be non-linear beyond 250k.
- Single run per size, no p95, no lock-contention test with concurrent import/run, not the reference VPS.
- RSS is the in-process test (API and worker in one process), not the separate `api` and `worker` containers of the real deployment; the per-process split is unmeasured.
- The memory-growth location is from code reading, not a profiler.
- Rebate-tier calculation and the claw-back path are not exercised by this volume file (SPA agreement only).

## 2026-11-14: import validator memory, before/after (branch `fix/import-memory-security-minors`)

Finding from S11.2: the import validator kept whole-file state per row (natural key as `str`, `(row, hex hash)` tuples, invoice-line key tuples, per-document header tuples), about 0.9 KB per row. `RowValidator` in `backend/src/rebate/modules/ingestion/validators.py` now keeps 16-byte BLAKE2b digests of the natural key, the invoice-line key and the document key, the row number as 6 big-endian bytes, the content hash as its first 16 bytes and the header as an 8-byte digest (all `bytes`). Validation results are unchanged: whole-file duplicate detection is still exact up to digest collisions (key digest 128 bits: about n^2/2^129, roughly 1e-27 at 1M rows; a wrong duplicate/conflict verdict needs a 128-bit content-hash-prefix collision on top of a key match; a wrong header-consistency verdict needs a 64-bit collision between two different headers of the same document). Credit-note originals keep their exact `(number, line)` tuples for the database lookup; only the "is it in this file" test uses digests.

| Measurement (same laptop/Docker as above, one run each) | Before | After |
|---|---|---|
| Validator state only: 100,000 sales rows (10% credit notes) through `RowValidator.validate`, RSS delta | 85 MB (0.87 KB/row) | 46 MB (0.47 KB/row) |
| `test_calculation_performance.py`, `PERF_ROWS=100000`, peak RSS during import (whole test process) | 386 MB (S11.2 run above) | 368 MB (370 MB `ru_maxrss`) |

The end-to-end reduction is small (about 18 MB, 5%) because the validator's dictionaries are only part of the import's footprint; the rest of the 368 MB is the in-process API and worker, the master-data index, SQLAlchemy and per-chunk buffers, which this change does not touch. The before figure for the end-to-end line is the S11.2 measurement, not a re-run on the same day. Wall time is noisy (two after-runs: 173 s and 123 s import) and is not compared. Projection from the validator-only slope: roughly 40 MB less per 100k rows, about 0.4 GB less at 1M rows; not measured at 1M. Not done: releasing state per chunk (duplicates and credit-note originals are whole-file rules), and per-document header state still grows with the number of documents.

## 2026-11-14: missed-claims list, before/after (branch `perf/missed-claims`)

NFR-04 target: list endpoints p95 < 500 ms. Same harness and generated files as the S11.2 section (`PERF_ROWS`, clock pinned to 2026-07-10), same laptop and Docker VM. Single warm in-process call, not p95. "Before" at 100k was re-measured on `main` (444970c) in this task, the 250k "before" is the S11.2 figure.

**Plans.** Before: `DISTINCT ON` over every result, external merge sort (3 x 6.5 MB), per-row `NOT EXISTS`, top-N sort, then `LIMIT 50`; 1,522 ms execution (S11.2), and about 1 s of that is JIT compilation triggered by the inflated cost estimate of the correlated recursive `NOT EXISTS`. After: `Limit` over an Index Scan Backward on `pk_calculation_results` with a per-row anti-join probe (`ix_calculation_results_tenant_id_sales_document_line_id` plus `uq_calculation_runs_tenant_id_id`) and a `claim_lines` index-only probe; it stops after 51 rows. 100k: 0.96 ms execution, 635 buffers, all hits. 250k: 1.02 ms, 633 buffers (flat in row count).

| Measurement | 100k before | 100k after | 250k before (S11.2) | 250k after |
|---|---|---|---|---|
| `GET /missed-claims?limit=50` (endpoint call) | 1,948 ms (re-measured; S11.2 run: 1,468 ms) | **48 ms** | 2,026 ms | **58 ms** |
| EXPLAIN ANALYZE execution time | 2,017 ms (S11.2: 1,522 ms) | 0.96 ms | 2,093 ms | 1.02 ms |
| Rows returned equal to the old query | n/a | 93,048 of 93,048, same order, all columns | n/a | 232,720 of 232,720 |

**What changed** (`backend/src/rebate/modules/claims/missed.py`, migration `20261121_0900_sdl_superseded_by`, no row semantics change):
- `LATEST` is an anti-join: a result is the newest of its line when no result of a completed run ranks above it on `(completed_at, id)`. Same order as the old `ORDER BY completed_at DESC, id DESC`; `completed_at` is never NULL for a completed run (`ck_calculation_runs_completed`). Used for bounded reads (`LIMIT`, explicit result ids). The old `DISTINCT ON` is kept as `LATEST_SCAN` for the summary and unbounded reads, where it measured faster than the anti-join (summary-shaped query: 0.2-0.4 s per pass versus about 1.2 s).
- `UNCLAIMED` checks the line's own active claim first, then walks only the strict ancestors. New partial index `ix_sales_document_lines_tenant_id_superseded_by_id` (`(tenant_id, superseded_by_id) WHERE superseded_by_id IS NOT NULL`, tiny). Found while testing: once any active claim exists the old recursive walk seq-scanned `sales_document_lines` for every candidate row (20,000 seeded claims at the head of the list: the old form did not finish in 2 minutes; the new one returned in 175 ms). The perf data has no claims, so the 48 ms figure does not include that case.
- `SET LOCAL jit = off` in `missed()` and `summary()`: the JIT compile (about 1 s on the summary, about 0.95 s on the list) outweighed the work.

**Equivalence proof.** `backend/tests/integration/missed_reference.py` keeps the old SQL as an oracle and compares every column and the row order for eligible and exception lines, for both `LATEST` variants. It runs (a) in `test_claims_batches.py` after a second run with claimed lines, (b) in `test_adjustment_flows.py` on a two-deep re-import chain with a claim on the first version, (c) at the end of `test_calculation_performance.py` on the 100k and 250k data (93,048 and 232,720 eligible rows identical).

**Not fixed / next step.** The summary endpoint still reads every result (about 0.8 s for its three queries at 100k with JIT off; was about 2.6 s with JIT; summary not measured end to end through the API). A real fix is a maintained `latest_result` projection. The list also walks past claimed or superseded rows: a tenant whose newest thousands of results are all claimed pays the skip cost (175 ms for 20,000 skipped); a projection (or a partial index on unclaimed eligible results) would bound that.
