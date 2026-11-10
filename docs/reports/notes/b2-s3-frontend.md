# b2-s3-frontend: S3 frontend notes

Branch `s3-frontend` (merged with `origin/s3-backend`; client regenerated from that backend).

## Built
- `frontend/src/features/masterdata/*`: manufacturers (+settings with If-Match/412 reload, reason-code mapping
  editor), customers (+parent, aliases), products (+aliases, product UoM), UoM conversions, branches,
  unmapped-entity queue (map via search picker, or ignore). Shared helpers in `common.tsx` (keyset pager, table).
- `frontend/src/features/imports/*`: history (filters, polling), new import (upload or existing file, type,
  source system, template, Idempotency-Key), import detail (processing / mapping with defaults and options and
  save-as-template / review with preview rows and issues / accept-partial / confirm / loading / result,
  row errors + CSV export link, changed-rows report), line browser for sales and purchases (URL filters,
  keyset pagination, lineage links to file+row and import batch) and sales line detail (versions, original line).
- "Suggest with AI" shows the backend's 409 `ai_not_enabled` message; no fake suggestions.
- 409 `too_many_running_imports` on start and confirm: server message plus a link to the Imports page.
- Routes and nav (`app/routes.tsx`, `app/app-layout.tsx`): Imports, Lines, Master data (data.read); new import
  needs import.run; unmapped queue needs masterdata.write.

## Outside owned files
- `frontend/src/features/files/files-page.tsx`: `uploadWithProgress` exported (reused by the import wizard).
- `frontend/src/api/openapi.json` regenerated from the merged backend (MappingIn key constraint).

## Backend gaps (for the backend owner)
- `GET /imports/{id}/errors` (JSON) has no response schema in OpenAPI; the UI mirrors RowErrorOut by hand
  (`import-detail-page.tsx`, marked) until the generated client carries it.
- No `GET /reason-codes` list, so the reason-code mapping editor takes the code as free text.

## Tests
- Vitest: `imports/logic.test.ts` (step machine, mapping init, required-field hint, defaults cleanup).
- Playwright: `e2e/imports.spec.ts` (master data via UI + wizard, layout A 23 inserted, re-import 0/23/0 from
  ground truth, error file counts/codes from ground truth, partial import, lineage link, unmapped resolve, axe on
  wizard/line/unmapped/imports pages), `e2e/masterdata.spec.ts` (CRUD + persistence + axe on detail pages),
  `e2e/a11y.spec.ts` (all new list pages). E2E runs only in CI: the shared dev stack runs main (no S3 API).
