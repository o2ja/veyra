# UI Report: S10 Dashboard and Reporting

## Screens Done & Not Done
- `/dashboard`: DONE. Seven server figure cards plus reserved "Recovered after write-off" card slot (renders only when server provides field), URL query filters for manufacturer, branch, and month (`YYYY-MM-01` without `new Date()` timezone shift), "As of {as_of}; at-risk computed for {as_of_date}" subtitle, "Refresh figures" button behind `can("report.refresh")` with queued notice and job polling, monthly table, and link to rebate tier tracker (`/calculations/rebates`).
- `/dashboard/:metric`: DONE. Drill-down screen with server `figure` and `total` summary cards, consistency warning banner ("Data changed since the last refresh") with refresh trigger when `consistent === false`, and cursor-paged table of line items with direct trace links.
- `/trace/:kind/:id`: DONE. Trace resolver showing ordered chain in an `<ol>` list from record to source file row, key-value tree for node fields, sub-trace navigation for supported trace kinds, "Aggregates many purchases: no single source row" note for rebate accrual, and 404 empty state for unknown kinds.
- `/reports/audit-pack`: DONE. Manufacturer and submitted claim batch selectors, "Generate audit pack (XLSX)" action (XLSX only, no CSV/PDF), async job polling, download URL generation, and permission notice for role 'analyst'.
- Screens not done: None.

## Verification Commands & Results
- `npm run typecheck`: PASSED (`tsc -b`, 0 errors).
- `npm run lint`: PASSED (eslint 0 errors / 0 warnings, Prettier code style check passed).
- `npx vitest run src/features/reporting`: PASSED (1 file, 4 tests passed).
- `npm test`: PASSED (7 test files, 38 unit tests passed).
- Stack check `curl -s http://localhost:8000/openapi.json | grep -c '"/api/v1/dashboard'`: returned 0 (dev stack offline / on another branch).
- `e2e/reporting-dashboard.spec.ts`: Written, not run (stack on another branch).

## Untested Items
- Playwright E2E test execution: not run (stack on another branch; orchestrator runs them).

## Gaps
- `recovered_after_write_off`: Backend does not yet populate this figure on `/dashboard` responses; reserved slot in UI displays nothing until field is returned.
- Audit pack 202 response body is untyped (`{}`) in OpenAPI schema (handled with safe type assertion).
- Release 1 is XLSX only (no PDF audit pack export by decision).

## Git Info & Commit Status
- Branch: `ui/s10`
- Last commit SHA: `9ed4643`
- Pre-commit hook: Windows sandbox `sh.exe` failure; commit hook not bypassed.
- Status: READY TO COMMIT

