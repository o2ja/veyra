# A1 S3 backend: resume notes (WIP, branch `s3-backend`)

## Done (built)
- Migrations `20261111_0900_masterdata` (down 20261110_1200_s2_review_fixes) and `20261111_1000_import_pipeline`
  (down 20261111_0900_masterdata). Models mirror; `tests/db` incl. `alembic check` PASS.
- `modules/masterdata`: models, service (CRUD, settings If-Match, reason-code mappings PUT, aliases, parent-cycle
  check, UoM CRUD, unmapped queue + resolve map/ignore, auto-resolve, bulk loads for imports), schemas, router, pagination.
- `modules/ingestion`: normalizers, readers (CSV encodings via files.decode_text, XLSX read-only/data_only), validators
  (fields per import type, suggest/check mapping, cross-row rules), loader (versioned upsert, ON CONFLICT, claims hook),
  service (job chain parse -> confirm -> chunked load with checkpoint), jobs, lines (browse/changes), idempotency
  (POST /imports), claims_hook (returns no claims before S7, documented), router.
- core/jobs: `JobContext.engine` + `renew_lease()`. files/validation: UTF-8/BOM/cp1252/UTF-16-BOM accepted, clear
  `encoding_unsupported`. files/__init__ exports get/read_clean/decode_text (outside owned files: flag in report).
- Permissions: `manufacturer_settings.write` (A P). Audit actions + AUDITED_FIELDS entries. Routers registered,
  worker registers ingestion jobs, import-linter contracts for both modules.
- origin/main merged (httpx2 fix).

## Tested
- tests/unit/test_ingestion_{normalizers,readers,validators}.py: 93 PASS.

## In progress (exact state)
- tests/integration/test_imports.py + conftest.py written, first run fails in fixture: `import_parse` after
  PUT mapping raises **TypeError** (job retry). Likely in service.validate (bulk `update(ImportStagedRow)` params,
  or `insert(ImportRowError)`), debug by calling validate directly / printing traceback. Also `.tuples()` deprecation
  warning in service.py ~415 (replace with plain rows).

## Remaining, in order
1. Fix TypeError; get tests/integration/test_imports.py green.
2. Tests: lease-outlive (monkeypatch jobs.LEASE short, threads, admin connection holding a lock, second worker
   reclaims, assert no duplicate rows); crash/resume mid-load (LOAD_CHUNK small, exception in 2nd chunk, retry);
   renew_lease unit test; masterdata API tests (CRUD, If-Match, audit rows); DB trigger tests (source rows immutable,
   no DELETE as app role); sales-lines/purchase-lines filters + pagination.
3. Performance test `@pytest.mark.performance` (register marker + deselect by default in conftest; pyproject not
   owned). 1M-row CSV ~150 MB exceeds 50 MB upload cap: seed file object directly; record rows/s + memory in notes.
4. Isolation: TENANT_TABLES + World rows for all new tables, D-series coverage, PARAM_SOURCES for new path params
   (manufacturer_id, customer_id, product_id, conversion_id, entry_id, import_id, template_id, line_id), route
   matrix `_call` must handle PUT; raise negative_control MIN_TESTS; run negative control.
5. Regenerate frontend client (`make gen-client` equivalent). Docs: DATABASE_SCHEMA (list_cost, severity, phase,
   batch columns, source_changed, currency on lines, upper-case canonical codes), API_SPECIFICATION (endpoints,
   ai_not_enabled 409, import_has_errors), SECURITY/ARCHITECTURE (encodings). Notes file `a1-s3-backend.md`.
6. Full suite + lint, push, PR, CI green, final report.
