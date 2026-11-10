# A1 S3 backend: resume notes (review fixes for PR #4, WIP)

Merged origin/main (edf5135: defusedxml, shm 1g). Helper to run tests: see COMMON env block, db `rebate_test_a1`.

## Done in code (state at the WIP commit)
- DB-1 natural key incl. doc_type: line unique indexes (migration 20261111_1000 edited in place + models), loader
  `_current_lines`/ON CONFLICT key, validator `_natural_key`. Test `tests/integration/test_source_integrity.py`
  (invoice 1001/1 + credit_note 1001/1 both current) PASSED.
- DB-2 currency trigger `fn_source_currency_check` on the 4 source tables (vs tenant_settings.currency_code);
  validator already reads tenant currency via `_lookups` (Lookups default "USD" only for unit tests). Test PASSED.
- DB-3 deferred constraint trigger `fn_source_successor_check` (self, missing, other key, version+1, current). PASSED.
- DB-4 column-level UPDATE grant (is_current, superseded_by_id) on source tables; isolation D3/D9 S3 adjusted. PASSED
  (test_app_role_may_update_only_the_versioning_columns). Isolation suite NOT yet rerun.
- DB-5 `_retire`: one UPDATE ... FROM (VALUES) with rowcount assert (ConcurrentLoad).
- SEC-7 XLSX: reset_dimensions, iter_rows(max_col), trailing-pad trim, MAX_EMPTY_RUN/TOTAL -> `too_many_empty_rows`.
  Tests in tests/unit/test_ingestion_readers.py PASSED (dimension A1:XFD1048576, column cap, empty floods).
- SEC-8 DEFUSEDXML test PASSED.
- SEC-9 MAX_ROWS 1,000,000; `TooManyRunningImports` (409 `too_many_running_imports`, MAX_RUNNING=3, advisory lock)
  in create_import + confirm; `_staged_window` (bounded PK row_number windows) for validate + load reads. NOT TESTED.
- SEC-10 audit in load_customers/products/uom_conversions (signature now `(db, who, rows, counts)`), NOT TESTED.
- SEC-11 parent-cycle check in load_customers -> `masterdata.ParentCycle`; load_chunk wraps load in a savepoint
  and fails the batch with code `customer_parent_cycle`. NOT TESTED.
- SEC-12 PAN: header with card number -> ReadError `card_number_in_header`; defaults with card -> mapping detail
  `card_number`; numeric XLSX 15-19 digit integers -> `[card number removed]` + PAN_MARK. NOT TESTED.
- SEC-14 tenant_id filters on staged-row reads + template lookup in preview; mapping keys `FieldName` (<=64,
  snake_case). NOT TESTED.

## Not done
- SEC-13 errors CSV export: stream (StreamingResponse), cap (e.g. 100k rows), audit (add Action
  `IMPORT_ERRORS_EXPORTED = "import.errors_exported"`), in ingestion/router.py `errors()`.
- Tests to write: SEC-9 (409 at 4th running import; MAX_ROWS), SEC-10 (audit rows after customers import),
  SEC-11 (file setting A parent of B when B parent of A in DB -> batch failed customer_parent_cycle, nothing loaded),
  SEC-12 (unit: card_like_integer, check_mapping defaults; integration: header card -> failed batch), SEC-13, SEC-14
  (422 on mapping key > 64 chars).
- Doc deltas into a1-s3-backend.md: DB-1 key, DB-2 trigger, DB-3, DB-4, DB-6 (§2 id exemption for
  manufacturer_settings + import_staged_rows; unmapped_entities.resolved_entity_id has no FK because it points at
  a customer OR a product), SEC-9 409 code, SEC-12 codes, SEC-15 note (idempotency stays tenant-scoped per spec).
- Run: ruff/mypy, touched tests, then full suite + isolation + negative control once; perf ONCE (if Postgres
  crashes: capture `docker logs veyra-recover-postgres-1` around it, stop, report); push; CI once; delete this file.
