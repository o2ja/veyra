# E2 — S3 gaps + end-to-end integration (branch `e2-s3-integration`, test db `rebate_test_e2`)
Read COMMON.md rules/environment, then ONLY: CLAUDE.md, API_SPECIFICATION.md (imports + reason codes parts),
BUSINESS_LOGIC.md §10.3 reason-code list, `modules/ingestion/{schemas,router}.py`, the reason-code parts of
`modules/masterdata`, `frontend/src/features/imports/`, `frontend/src/features/masterdata/manufacturers-pages.tsx`,
`testdata/generated/{master,sales,purchases,agreements/manual_payloads}`, `ground_truth/expected_calculation.json`,
`expected_rebates*.json`, existing `tests/integration/test_calculation_runs.py` (pattern).

Do:
1. Import row errors: replace the hand-written `RowErrorOut` frontend type with the published OpenAPI schema
   (publish it from the backend if it is not published), regenerate the client (`frontend/src/api/`) the way
   the repo does it, and use the generated type.
2. Reason codes: a controlled list (the §10.3 codes) enforced on the backend (enum/constraint as the docs say,
   migration if needed — revision id ≤ 32 chars, chained on main's head), published in OpenAPI, regenerated
   client, and the small S3/masterdata UI cleanup to use it (select instead of free text where it is free text).
3. "Suggest with AI" in `features/imports/import-detail-page.tsx`: on 200 `MappingOut` show the suggested
   mapping with an "Apply suggestion" button (never auto-save); 409 `ai_not_enabled` / 503 messages as returned;
   vitest for any pure logic. (Live AI is unavailable; the e2e covers the 409 path only.)
4. End-to-end integration test on synthetic data (real Postgres, real services, no mocks): import the testdata
   master + sales (+ purchases if a rebate uses them) through the ingestion service → create and approve the
   agreements from `manual_payloads` through the agreements service → run a calculation run → assert every
   result against `expected_calculation.json` and every accrual against `expected_rebates*.json` (exact Decimal).
   If a value does not match: STOP, do not change expectations or code to fit; report it (financial discrepancy).
Own only those files plus your new tests and migration. API doc deltas go in `docs/reports/notes/e2.md`
(another agent edits API_SPECIFICATION.md). Another agent (Antigravity) builds `features/calculations` and
`features/extractions` on `ui/s5-s6` — do not touch them.
