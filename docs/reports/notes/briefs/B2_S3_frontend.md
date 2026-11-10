# B2 — S3 frontend: master data and import wizard

Agent id: `b2-s3-frontend`. Branch: `s3-frontend`. No backend changes (backend S3 is on main).

## Read
`CLAUDE.md` §10 (frontend rules); `IMPLEMENTATION_PLAN.md` §S3 (S3.1 "frontend grids and forms", S3.5);
`PRODUCT_REQUIREMENTS.md` §1.4 and the UX parts; `API_SPECIFICATION.md` (master data, imports, sales-lines,
purchase-lines); `TESTING_STRATEGY.md` (frontend/e2e). Existing patterns: `frontend/src/features/files/*`,
`features/admin/*`, `app/routes.tsx`, `app/app-layout.tsx`, the generated client in `frontend/src/api`,
`frontend/e2e/*.spec.ts` (fixtures, axe checks).

## Build
- Master data grids and forms: manufacturers (+settings), customers (+aliases, parent), products (+aliases), UoM
  conversions, branches, reason codes/mappings; unmapped-entity queue with resolve actions.
- Import wizard: upload (reuse files upload), choose type, mapping UI (save/reuse templates), preview, row errors,
  partial-import choice, confirm, progress; import history; changed-rows report; line browser (sales/purchases,
  filters, keyset pagination) with lineage links to source file and row. `suggest-mapping` shows the backend's
  "AI not enabled" state honestly (no fake suggestions).
- Permission-aware navigation; every screen keyboard accessible; generated client only (no hand-written fetch).

## Tests
Vitest for non-trivial UI logic; Playwright e2e (real backend, real worker, the testdata layout A sales file):
upload → map → preview → confirm → counts match `expected_import_results.json`; re-import no-op; error rows
visible; line browser lineage link; axe on every new page. To run e2e you need the dev stack running YOUR
branch's frontend: use the e2e approach in `.github/workflows/ci.yml` / existing e2e config; if it requires
restarting shared containers, do NOT — rely on CI's e2e job for the full run and run Vitest/lint/typecheck
locally; say so in the report.

## You own
`frontend/src/features/{masterdata,imports}/**`, `frontend/src/app/{routes.tsx,app-layout.tsx}` (add entries),
`frontend/e2e/{imports,masterdata}.spec.ts`, `frontend/e2e/a11y.spec.ts` (add pages). Nothing in `backend/`.
If the backend API is missing something the UI needs, report it — do not change the backend.
