# a1-s3-backend: S3 backend notes (branch `s3-backend`)

For the orchestrator: built scope, doc deltas to merge, deviations, test map, performance baseline.

## 1. Built

- Migrations `20261111_0900_masterdata` (down_revision `20261110_1200_s2_review_fixes`) and
  `20261111_1000_import_pipeline` (down_revision `20261111_0900_masterdata`). Models mirror them; `alembic check` clean.
- `modules/masterdata`: CRUD for manufacturers (+settings with `If-Match` revision), reason-code mappings (PUT
  replace), customers (+aliases, parent with cycle check), products (+aliases), UoM conversions, branches,
  reason codes (seeded, read-only for `rebate_app`), unmapped-entity queue (map / ignore, auto-resolve).
- `modules/ingestion`: normalisers, readers (CSV via `files.decode_text`; XLSX openpyxl read-only + data_only),
  validators, versioned loader (`ON CONFLICT` natural key + row hash), claims hook, job chain
  parse -> (confirm) -> chunked load (`LOAD_CHUNK = 5000`, one job + transaction per chunk, checkpoint in
  `import_batches.checkpoint`), browse APIs for sales/purchase lines with lineage, changes report.
- `core/jobs.py`: `JobContext.engine` and `renew_lease()` (attempt-conditional, raises `LeaseLost`).
- `modules/files/validation.py`: CSV accepted in UTF-8, UTF-8 BOM, Windows-1252, UTF-16 LE/BE with BOM;
  `encoding_unsupported` (422) for anything else.
- Permissions: new `manufacturer_settings.write` (admin, approver). Audit `Action` entries + `AUDITED_FIELDS`.
  Routers registered in `api/app.py`, ingestion jobs in `workers/__main__.py`, import-linter contracts.
- Frontend client regenerated (`frontend/src/api/openapi.json`, `schema.d.ts`) through the `gen-client` commands.

## 2. Files touched outside the brief's owned list (flag for review)

- `backend/src/rebate/modules/files/__init__.py`: exports `get`, `read_clean(settings, row)` (bytes of a file
  whose scan is `clean`, `InvalidState` otherwise), `decode_text`, `UndecodableText`. The importer needs these
  through the files public API (modules call services, not foreign tables). Not in the owned list.
- `backend/tests/conftest.py` (owned: tests/**): registers the `performance` marker and deselects it by default
  (pyproject is not owned, so not via `addopts`).

## 3. Doc deltas (to merge into the owning docs)

### DATABASE_SCHEMA.md §3.3–3.5
- `products`: add `list_cost NUMERIC(18,6)` (nullable, >= 0; reference cost from the product master file).
- `manufacturer_settings`: add `revision int` (optimistic concurrency, ETag/If-Match on PUT).
- `mapping_templates`: `header_signature char(64)` (sha256 hex of normalised headers), `options jsonb NN default {}`,
  `updated_by`, `updated_at`.
- `import_batches`: add `mapping_template_version`, `detected_encoding` (`utf-8`,`utf-8-sig`,`cp1252`,`utf-16-le`,
  `utf-16-be`,`xlsx`), `delimiter`, `sheet_name`, `header_row`, `headers jsonb`, `header_signature`, `mapping jsonb`,
  `options jsonb`, `mapping_revision`, `updated_at`, `confirmed_by`, `confirmed_at`. `counts` keys:
  `rows_total, valid, duplicates, rejected, inserted, unchanged, changed, source_changed`.
  CHECK: `completed_at` set iff status is `completed`/`completed_with_errors`.
- `import_staged_rows`: add `tenant_id`, `natural_key`, `row_hash`, `phase smallint` (1 = invoices/receipts,
  2 = credit notes/vendor credits, loaded after their originals), status `pending|valid|error|duplicate`
  (no `changed`: changed is a load outcome, counted on the batch).
- `import_row_errors`: add `severity` (`error`|`warning`), `created_at`.
- Source tables: `sales_documents`/`purchase_documents` and their lines carry `doc_type`, `currency_code`
  (ISO 4217, upper case) and `source_system`; lines add `source_changed bool NN default false`; purchase
  documents carry `po_number`; `sales_documents` carry `branch_id`, `original_document_id`.
- Canonical codes are upper case, stated as CHECKs: UoM `^[A-Z0-9]{1,10}$`, `products.sku = upper(sku)` (leading
  zeros kept, e.g. `00418`), manufacturer codes.
- Source tables: `BEFORE UPDATE` trigger `fn_source_row_versioned` (only `is_current`/`superseded_by_id` may
  change, once), `BEFORE DELETE/TRUNCATE` forbid, `DELETE/TRUNCATE` revoked from `rebate_app`.

### API_SPECIFICATION.md
- §2.2 files: replace "CSV must be UTF-8/ASCII text (UTF-16 ... rejected as `content_type_mismatch`...)" with:
  "CSV text may be UTF-8 (with or without BOM), Windows-1252 or UTF-16 LE/BE with BOM; other content →
  422 `encoding_unsupported`".
- §2.3 master data: `PUT /manufacturers/{id}/settings` needs `If-Match: "<revision>"` (412 on mismatch, permission
  `manufacturer_settings.write`, A P); `PUT /manufacturers/{id}/reason-code-mappings` replaces the set;
  `PUT/DELETE /uom-conversions/{id}`; invalid settings or UoM pairs → 422.
- §2.4 imports: `POST /imports` requires `Idempotency-Key`; `POST /imports/{id}/suggest-mapping` → **409
  `ai_not_enabled`** (no job, no output) until the AI gateway stage; `POST /imports/{id}/confirm` with errors and
  `accept_partial=false` → **409 `import_has_errors`**; `PUT /mapping-templates/{id}` needs `If-Match: "<version>"`
  (412); `GET /imports/{id}/errors?format=csv` is formula-injection safe; `/sales-lines` filters
  `manufacturer_id, customer_id, product_id, import_batch_id, document_number, doc_type, date_from, date_to,
  current` (default `true`), `/purchase-lines` the same without `customer_id`; every line carries `lineage`
  (`import_batch_id, file_id, source_row_number, source_system, version, is_current, superseded_by_id`);
  `GET /sales-lines/{id}` adds `previous_version_id`. There is no `GET /purchase-lines/{id}` (not in the spec).
  "claim status" filter on sales-lines is not built (claims arrive in S7).

### SECURITY.md / ARCHITECTURE.md (encodings)
- SECURITY.md §4: added a "CSV text encoding" row (edited directly, brief allows). ARCHITECTURE.md has no
  UTF-8-only sentence; nothing changed there.

### PR #4 review fixes (DB-1..DB-6, SEC-9..SEC-15): deltas for DATABASE_SCHEMA / API_SPECIFICATION / SECURITY

- **DB-1 natural key** of the four source tables includes `doc_type`: unique indexes `WHERE is_current` are
  `(tenant_id, source_system, doc_type, document_number)` on documents and `(..., line_number)` on lines (migration
  `20261111_1000_import_pipeline` edited in place: unreleased branch). An invoice 1001/1 and a credit note 1001/1
  are two current lines. Loader `ON CONFLICT` target and validator duplicate key use the same columns.
- **DB-2** trigger `fn_source_currency_check` (BEFORE INSERT/UPDATE on the four source tables): `currency_code`
  must equal `tenant_settings.currency_code`. The validator reports the same rule per row (`currency_mismatch`).
- **DB-3** deferred constraint trigger `fn_source_successor_check`: a retired line (`is_current = false`) must have
  `superseded_by_id` pointing at a different, existing, current row of the same natural key with `version + 1`.
- **DB-4** `rebate_app` has column-level `UPDATE (is_current, superseded_by_id)` only on the source tables (no
  UPDATE of business columns; no DELETE). Isolation D3/D9 adjusted for S3 tables.
- **DB-5** retirement is one `UPDATE ... FROM (VALUES ...)` per chunk with a rowcount check (concurrent load -> fail).
- **DB-6** §2 exemptions: `manufacturer_settings` (PK = `manufacturer_id`, 1:1 extension) and
  `import_staged_rows` (PK = `(import_batch_id, row_number)`, bounded PK range scans) have no own `id`.
  `unmapped_entities.resolved_entity_id` has no FK because it points at a customer OR a product (`entity_type`
  says which); the resolve service checks the target exists in the tenant.
- **SEC-9** limits: `MAX_ROWS` 1,000,000 data rows per file (batch fails, row error `too_many_rows`); at most 3
  imports per workspace in `scanning/parsing/loading` (checked under a per-tenant advisory lock on create and
  confirm): `409 too_many_running_imports`. Add to API_SPECIFICATION error codes for `POST /imports` and
  `POST /imports/{id}/confirm`.
- **SEC-10** master-data imports write one audit event per created/changed customer, product and UoM conversion
  (`customer.created/updated`, `product.created/updated`, `uom_conversion.created/updated`) in the chunk's
  transaction, actor = the confirming user, `request_id = import:<batch id>`.
- **SEC-11** customer parent cycles: before the first chunk of a customers import, the whole file's parent
  assignments are applied on top of the workspace hierarchy and checked for cycles
  (`masterdata.check_parent_cycles`); a cycle fails the batch with row error `customer_parent_cycle` and nothing
  of the import is loaded. The per-row check inside the chunk (savepoint) stays as a second guard.
- **SEC-12** PAN guard codes: `card_number_in_header` (batch failed at parse; headers are not stored),
  `card_number` (422 detail on `options.defaults.<field>` of `PUT /imports/{id}/mapping` and templates),
  `possible_card_number` (row error). Numeric XLSX cells holding a 15-19 digit integer are replaced by
  `[card number removed]` without a Luhn check (Excel keeps 15 significant digits, so Luhn may no longer pass).
- **SEC-13** `GET /imports/{id}/errors?format=csv`: streamed (`text/csv`), at most 100,000 rows in file order,
  response header `X-Export-Truncated: true|false`, audit event `import.errors_exported` (metadata `rows`,
  `truncated`). Formula-injection protection unchanged. Rows are read inside the request transaction; only the
  CSV formatting is streamed.
- **SEC-14** staged-row and template reads filter `tenant_id` explicitly (besides RLS); mapping keys are
  `snake_case`, 1-64 chars (422 from the schema), at most 50 entries.
- **SEC-15** idempotency keys stay tenant-scoped (`(tenant_id, key)`) as API_SPECIFICATION says; no change.

### Claims hook (DATABASE_SCHEMA §5 / BUSINESS_LOGIC §7 note)
`modules/ingestion/claims_hook.claimed_line_ids(db, tenant_id, line_ids) -> frozenset[UUID]` is real code that
returns the empty set because no `claim_lines` table exists before S7 (documented in the module). The loader
calls it for every superseded line and sets `source_changed` on the new version for each returned id; S7 replaces
the body with the claim-lines query and opens the variance case. Tested with a substituted hook
(`test_claimed_line_change_is_flagged_through_the_hook`) and for the S3 answer (`test_hook_reports_no_claims_before_s7`).

## 4. Deviations

- `sales_with_errors_layoutA.csv`, INV-30004 (unknown product): ground truth says "to unmapped queue, not a hard
  error". The row is held (reported as an error row with code `unknown_product`, counted in `rejected`) and the
  SKU goes to the unmapped queue; after the user maps it, re-importing the file loads it. Counts match the ground
  truth (`rows_total 8, valid_inserted 1, duplicate_in_file 1`).
- Performance: the 1M-row file (163 MB) exceeds the 50 MB upload cap; the test seeds the file object directly
  (object storage + `clean` row) and runs the real parse/validate/load chain. The generator needs `reportlab`,
  which the api image does not have: it was installed into `/tmp` of a throwaway container only (no repo/image change).

- `tests/api/test_route_access.py` and `tests/support.py`: the route matrix sent PUT as PATCH (405); added
  `Api.put` and used it. `migrations/.../20261111_0900_masterdata.py`: reason-code seed now uses bound parameters
  (bandit B608).

## 5. Acceptance criteria → tests (IMPLEMENTATION_PLAN §S3, backend parts)

Integration tests run through real upload -> ClamAV scan -> job chain on Postgres + SeaweedFS
(`tests/integration/test_imports.py` unless noted). Expected values come from `testdata/generated/ground_truth`
or a hand derivation from the files and BUSINESS_LOGIC §7, never from the code's output.

| Criterion | Test(s) |
|---|---|
| **Acceptance:** layout A imports with expected counts | `test_layout_a_imports_reimport_is_a_noop_and_lineage` (23 lines), unit `test_scenarios_layout_a_all_valid_and_credit_notes_negative` |
| **Acceptance:** layout B (xlsx, US formats, title rows, leading zeros) | `test_layout_b_xlsx_us_formats_and_saved_template_reuse` (rows_total 3 = GT), unit `test_layout_b_us_formats`, `test_layout_b_with_dmy_*` |
| Error file vs ground truth (rows_total 8, valid 1, duplicate 1, rejected codes) | `test_error_file_partial_import_and_unmapped_queue`, unit `test_error_file_matches_ground_truth` |
| **Acceptance:** re-import is a no-op (GT `reimport_same_file` 0/23/0) | `test_layout_a_imports_reimport_is_a_noop_and_lineage` |
| **Acceptance:** changed rows versioned and reported (`expected_changed_rows.json`: 2 changed INV-10001/1, INV-10002/1, 3 unchanged) | `test_changed_rows_are_versioned_and_reported`; source_changed via hook: `test_claimed_line_change_is_flagged_through_the_hook`, `test_hook_reports_no_claims_before_s7` |
| **Acceptance:** every line traceable to file and row | `test_layout_a_*` (row N ↔ CSV record, `lineage.file_id`, `source_row_number`), `test_purchase_line_filters_and_pagination` (`tests/integration/test_masterdata.py`) |
| Master files (customers, products, uom) | `test_master_data_files_load`, unit `test_master_files_validate`, `test_customer_parents` |
| Purchases incl. vendor credit sign; volume file | `test_purchases_hvac_and_volume` (TEST_DATA_SPEC §2.4: 990,000 total, VCR qty -200), unit `test_purchases_vendor_credit_negative` |
| Order independence | `test_order_independence` |
| Crash/resume; lease outlived + reclaim, no duplicates; lease renewal | `tests/integration/test_import_resume.py` (3 tests) |
| Locale formats (dates, numbers, separators, parentheses) | unit `test_numbers`, `test_bad_numbers`, `test_dates`, `test_bad_dates`, `test_scale_and_range_are_errors_not_rounding`, `test_decimal_text_round_trips_losslessly` (Hypothesis) |
| Credit-note sign canonicalisation | unit `test_credit_notes_are_negative_whatever_the_file_says`, `test_sign_errors`; integration CN-5001 -5 / -290 in `test_layout_a_*` |
| S3.1 master data CRUD, If-Match, audit, unmapped queue | `tests/integration/test_masterdata.py` (`test_manufacturers_and_settings`, `test_reason_code_mappings`, `test_customers_aliases_parents`, `test_products_uom_branches_and_pagination`, `test_unmapped_ignore_and_auto_resolve`) |
| S3.2 readers: encodings/BOM/delimiter, XLSX read-only, no formulas | unit `test_every_supported_encoding_decodes_to_the_same_rows`, `test_undecodable_content_has_a_clear_user_facing_error`, `test_delimiter_is_sniffed`, `test_xlsx_cells_become_text_without_float_noise_and_formulas_are_not_evaluated`, `test_title_rows_are_skipped_and_empty_rows_dropped`; integration `test_encodings_through_upload` (UTF-8, BOM, cp1252, UTF-16 LE/BE), `test_undecodable_upload_is_rejected_with_a_clear_message` |
| S3.2 normalisers SKU/UoM, header signature, templates (save/reuse/version) | unit `test_codes_keep_leading_zeros_and_fold_case`, `test_uom*`, `test_header_signature_ignores_order_case_and_spacing`; `test_layout_b_*` (reuse, If-Match versioning) |
| S3.3 validation: required/ranges/referential/original link/currency/PAN | unit `test_unknown_customer_currency_negative_price_and_orphan_credit_note`, `test_pan_guard_marker_rejects_the_row`, `test_luhn_and_pan_guard`; integration `test_pan_guard_never_stores_card_numbers` |
| S3.3 pipeline states, infected file, idempotency, AI not enabled, import_has_errors | `test_infected_file_is_rejected_by_the_chain`, `test_idempotency_key_replays_and_conflicts`, `test_suggest_mapping_says_ai_is_not_enabled`, `test_mapping_validation_and_state_rules`, `test_unreadable_file_fails_the_batch_visibly`, `test_error_file_*` (409) |
| S3.4 browse APIs: filters + keyset pagination | `test_sales_line_filters`, `test_purchase_line_filters_and_pagination` |
| Source rows immutable/versioned only, no DELETE | `test_source_rows_are_versioned_only`, isolation `test_d9_s3_source_tables_cannot_be_deleted_or_moved` |
| Tenant isolation for every S3 table and route | `tests/isolation`: guard `test_every_tenant_isolated_table_is_in_the_isolation_suite`, D1/D7/D8/D12 (all tables), S3 D2/D3/D5 (18 tables), D4/D6 (14 non-source), R1 (every parameterised route incl. PUT), R2 S3 lists + foreign-id filters, R3 S3 mutations |
| Performance baseline | `tests/integration/test_import_performance.py` (`-m performance`), §6 |

## 5a. Isolation results (2026-11-10)

- `pytest tests/isolation`: 233 passed; `tests/db/test_schema_lint.py tests/db/test_migration_helpers.py`: 7 passed.
- Negative control (`python -m tests.isolation.negative_control`, db `rebate_negctl`): **OK**, 121 RLS-dependent
  tests failed with ISOLATION-BREACH, all independent tests (grants, route matrix incl. every S3 route) passed.
  `MIN_TESTS` raised 64 -> 233.

Rerun after the PR #4 review fixes (2026-11-10): full backend suite 1162 passed / 4 failed (isolation D5 on the
four source tables: the new currency trigger refused the foreign-tenant insert before RLS WITH CHECK could;
fixed by letting the trigger skip rows whose tenant is not the session's, which RLS always refuses). After the
fix: `tests/isolation` + schema lint + migration helpers + `test_source_integrity.py` 247 passed; negative
control OK (121 RLS-dependent tests detected the breach, all independent tests passed).

## 6. Performance baseline (1M rows): RECORDED 2026-11-10 (third attempt overall, first after the fixes)

Result (one run, shared dev stack, Postgres `shm_size` 1g, Docker VM 7.6 GiB, 12 CPUs, other agents' load present):
`PERF rows=1000000 bytes=163437435 parse+validate=362.1s (2761 rows/s) load=714.5s (1400 rows/s)
total=1076.6s (929 rows/s) peak_rss=1184MB (before 239MB)`; all 1,000,000 lines inserted, batch `completed`,
no Postgres crash. Postgres container peaked at about 1.65 GiB during the load. Peak RSS is the test process,
which runs API and worker in-process. The history of the earlier failed attempts follows.


File: `testdata/generate.py --volume 1000000 --out <scratch>` -> `sales_volume_1000000.csv`, 163,437,435 bytes,
1,000,000 data rows (not committed). Test: `tests/integration/test_import_performance.py`, run with
`-m performance -s` and `PERF_SALES_CSV=<path>` (file seeded directly; the 50 MB upload cap is not lifted).

What was measured (2026-11-10, shared dev stack, Docker VM 7.6 GiB, 12 CPUs):
- Run 1: parse + validate + staging of 1M rows completed (1,000,019 staged rows, 597 MB `import_staged_rows`).
  The load then ran at about 5,000 rows per 20 s chunk and slowing. Cause, measured with EXPLAIN ANALYZE: the
  row-value `(doc_type, document_number) IN (...)` lookups over 5000 keys took **8.9 s planning** (0.6 s execution)
  per query. Fixed in `loader.py`: scalar `IN` (= `ANY(array)`) on the natural-key index columns, exact keys
  filtered in Python (integration suite green after the change).
- Runs 1 and 2: shortly after staging, the shared Postgres server lost a backend
  (`server process ... exited with exit code 2`, 17:04:07 and 17:35:02 UTC), restarted all backends and recovered
  in about 15 s. The test failed both times on the first chunk read (`SELECT ... FROM import_staged_rows ... LIMIT
  5000`). Memory samples during run 2: Postgres container ≤ 2.6 GiB, test container ≤ 0.9 GiB; data disk 2 % used;
  `/dev/shm` is 64 MB (compose default) with `dynamic_shared_memory_type=posix`. Root cause not determined; I
  stopped retrying because each attempt crashes the database other agents share.
- Not recorded therefore: end-to-end rows/s and peak RSS. Suggested next step (orchestrator): run the perf test
  once on a dedicated Postgres (or with `shm_size` raised for the postgres service), and check whether
  `import_staged_rows` needs an `ANALYZE` after staging (planner estimated 2,024 rows for a 1M-row batch).
