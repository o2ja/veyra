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
