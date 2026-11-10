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

## 5. Acceptance criteria → tests (IMPLEMENTATION_PLAN §S3, backend parts)

## 6. Performance baseline (1M rows)
