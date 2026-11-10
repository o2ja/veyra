# A1 S3 backend: resume notes (WIP, branch `s3-backend`)

Built and earlier test results: see `a1-s3-backend.md` §1 (folded in). Items 1-2 of the original list are done
(commit b03bf80).

## Written in session 3, NOT yet run against Postgres (shared Docker services were down: all
`veyra-recover-*` containers Exited (255); COMMON forbids starting them)
- `tests/isolation/conftest.py`: TENANT_TABLES + one World row per S3 table (`TenantData.s3`).
- `tests/isolation/test_db_rls.py`: guard `test_every_tenant_isolated_table_is_in_the_isolation_suite`
  (pg_policies tenant_isolation == TENANT_TABLES), S3 D2/D3/D5 (all 18 tables), D4/D6 (14 non-source tables),
  `test_d9_s3_source_tables_cannot_be_deleted_or_moved` (passes with RLS off by design).
- `tests/isolation/test_routes.py`: S3 seed per side (`_seed_s3`), PARAM_SOURCES for 8 new params, `_call` PUT
  with If-Match "1", per-path valid BODIES, S3 list R2, foreign-id filter R2, S3 R3.
- `tests/integration/conftest.py`: `seed_master`, `walk` helpers. `test_masterdata.py`: sales-lines pagination,
  `test_purchase_line_filters_and_pagination`.
- `tests/integration/test_import_performance.py` (+ marker/deselect in tests/conftest.py).
- Lint (ruff, format, mypy) green; unit validators green. Client regenerated. SECURITY.md encoding row.

## Remaining, in order
1. Run: tests/integration/test_masterdata.py -k "line or products_uom"; tests/isolation (all); fix.
2. Count isolation tests, set negative_control MIN_TESTS to that count; run negative control (db rebate_negctl):
   `REBATE_TEST_DB=rebate_negctl python -m tests.isolation.negative_control`.
3. Perf: 1M file already generated at scratchpad `perf/gen/sales/sales_volume_1000000.csv` (163 MB; regenerate:
   ephemeral `pip install --target /tmp/gd reportlab`, `PYTHONPATH=/tmp/gd python /testdata/generate.py
   --volume 1000000 --out /perf/gen`). Run with `-v <dir>:/perf -e PERF_SALES_CSV=/perf/gen/sales/...
   pytest -m performance -s tests/integration/test_import_performance.py`; record into notes §6.
4. Fill notes §5 (criterion -> test), full suite + lint once, delete this file, commit, push, PR, CI.
