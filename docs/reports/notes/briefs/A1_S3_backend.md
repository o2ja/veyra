# A1 — S3 master data and import pipeline (BACKEND)

Agent id: `a1-s3-backend`. Branch: `s3-backend`. Test DB: `rebate_test_a1`. You MAY run the isolation suite and
its negative control (`make test-isolation` equivalent; you are the only DB agent in this wave).

## Read
`CLAUDE.md`; `docs/IMPLEMENTATION_PLAN.md` §S3 (tasks S3.1–S3.4, S3.6; acceptance); `PRODUCT_REQUIREMENTS.md` §1.4;
`DATABASE_SCHEMA.md` §3.3–3.5 and §5; `BUSINESS_LOGIC.md` §7 (sign rules); `TEST_DATA_SPEC.md`; `SECURITY.md` §4
(uploads/import hardening, formula/CSV injection, PAN guard); `API_SPECIFICATION.md` (imports, master data,
sales-lines/purchase-lines); `ARCHITECTURE.md` §2–§3, §6 (jobs); `TESTING_STRATEGY.md`;
`testdata/generated/{sales,purchases,master}` and `ground_truth/expected_import_results.json`,
`expected_changed_rows.json`, `testdata/generated/README_SYNTHETIC.txt`, `testdata/generate.py` (`--out` for 1M rows).
OD-1 is APPROVED: first vertical is electrical (ADR-019) — use its UoM/terminology defaults.

## Build (all backend; the frontend is a separate agent later)
- S3.1 master data: migrations + models + service + router + CRUD for manufacturers (+settings), customers
  (+aliases, parent), products (+aliases), UoM conversions, branches, reason codes seed, reason-code mappings;
  unmapped-entity queue with resolve actions. Module `modules/masterdata`.
- S3.2 readers/normalisers (pure, heavily unit-tested): CSV reader with encoding/BOM/delimiter detection, XLSX
  reader (openpyxl read-only, strings/values only, header-row detection, title-row skipping, no formulas
  evaluated, no macros), normalisers (dates incl. US formats, numbers with thousands/decimal separators and
  parenthesis negatives, SKUs preserving leading zeros/case rules, UoM codes, credit-note sign canonicalisation),
  header signature hashing, mapping templates (save/reuse/version). Module `modules/ingestion`.
- S3.3 pipeline: `import_batches`, `import_staged_rows`, `import_row_errors`; job chain scan → parse → (confirm)
  → load using `core/jobs.py`; validation (required, ranges, referential, credit-note original-invoice link,
  currency, Luhn/PAN guard); loader chunked + resumable, `ON CONFLICT` natural key + row hash →
  inserted/unchanged/changed, versioned rows, `source_changed` flag for claimed lines via a clearly documented
  hook that S7 completes (the hook must be real code that returns "no claims exist" because no claims table
  exists yet — document it; do not fake claims); import types sales, purchases, customers, products, uom;
  `suggest-mapping` endpoint returns a clear "AI not enabled" problem response (no fake output).
- S3.4 source tables + browse APIs `sales-lines`, `purchase-lines` with filters and keyset pagination; every
  line traceable to file and row (lineage).
- Permissions for every new route (tenancy permission catalog), audit events for every mutation, RLS on every
  new tenant table, extend `tests/isolation` (World fixture, D-series in `test_db_rls.py`, route checks) for
  every new tenant table and route; the RLS-off negative control must still detect breaches.

## Mandatory requirements from the S2 report
1. **Job lease** (`core/jobs.py`, 10-minute lease, no heartbeat today): the S3 loader must either work in chunks
   that each finish well inside the lease (each chunk its own job/transaction, resumable from a committed
   checkpoint) or renew the lease while running (you may add a lease-renewal API to `core/jobs.py`; keep the
   attempt-conditional `_mine`/`LeaseLost` safety). Add a test with a job that outlives the lease (use a short
   lease in the test, real Postgres, a second worker reclaiming) and prove no row is loaded twice.
2. **Encodings**: accept UTF-8, UTF-8 with BOM, Windows-1252 and UTF-16 (LE/BE with BOM) CSV. Detect, decode,
   record the detected encoding on the import batch. Update S2 upload validation
   (`modules/files/validation.py`, currently UTF-8-only) to allow them, keep every other S2 check. Tests for each
   encoding (bytes built in the test, e.g. `"Café".encode("cp1252")`), plus a clear user-facing error for
   undecodable content. Update `SECURITY.md`/`ARCHITECTURE.md` where they say UTF-8 only.
3. Isolation suite + negative control extended to every new tenant table and route.

## Tests (S3.6)
All `testdata/generated/sales`, `purchases`, `master` files vs `expected_import_results.json` (and
`expected_changed_rows.json`); idempotent re-import (no-op); changed rows versioned and reported; order
independence; crash/resume (kill mid-load, resume, no duplicates); locale formats; credit-note sign; performance
baseline: generate 1M rows with `testdata/generate.py --out <dir>` into a scratch dir (not committed), import,
record rows/s and memory in your notes (mark the test `@pytest.mark.performance`, excluded from the default run).
Real Postgres, SeaweedFS, ClamAV — files go through the real upload + scan path in integration tests.

## You own
`backend/src/rebate/modules/{masterdata,ingestion}/**`, `backend/src/rebate/modules/files/validation.py`,
`backend/src/rebate/core/jobs.py` (lease work only), new files under `backend/migrations/versions/` (start after
`20261110_1200_s2_review_fixes`), registration lines in `api/app.py`/routers aggregation and
`workers/__main__.py`, the tenancy permission catalog and audit `Action` enum (add entries only),
`backend/importlinter.ini` (add contracts for your modules), `backend/tests/**` except `tests/engine*` and
`tests/extraction/**`, `frontend/src/api/**` (regenerate the client via the `gen-client` Makefile commands so the
CI drift check passes; no other frontend files), `docs/DATABASE_SCHEMA.md`, `docs/API_SPECIFICATION.md`,
`docs/SECURITY.md`, `docs/ARCHITECTURE.md` (S3 sections only).
Do NOT touch `backend/src/rebate_engine/**`, `modules/extraction/**`, other frontend code, pyproject/uv.lock
(deps already installed: openpyxl, charset-normalizer).
