# UI brief: S10 dashboard and reporting (branch `ui/s10`, self-contained)

**Goal.** Build the dashboard (figures with filters, monthly view and drill-down), the trace resolver ("explain this
number" from any figure down to the source file row), the audit pack (XLSX only for Release 1) and a link to the existing tier
tracker. Everything uses the real API. **The server is the only authority for every figure.**

**Base.** Branch `ui/s10` already exists in the worktree `D:/veyra/veyra-ui-s10`: backend branch s10-reporting (S10 core incl. the owner figure decisions; review pending), with `ui/s7` merged in. It is not on main yet; the orchestrator merges the backend and this branch later. Do not merge, rebase or wait for main.

## Owner decisions (2026-11-13): the server implements them, the UI only labels them
- **Claimed** = claim batch totals including carried adjustments. **Collected** = cash on matched remittance lines.
- **One** at-risk definition, shared by `/dashboard` and `/cases/summary`.
- Month buckets use the tenant business time zone (the server decides). The UI sends and shows the server's `month`
  date strings as plain text. **Never** pass them through `new Date()`, which shifts the day in negative UTC offsets.
- A separate **"Recovered after write-off"** figure. Reserve its card slot in the layout (after "Written off"). Render
  it only once the regenerated client has the field. Until then render nothing, with no placeholder, and record it as a gap.
- The `m1-s10` branch as read still has claimed *without* adjustments and two at-risk definitions. If main still
  does, record it as a gap and do not compensate in the UI.

## Pattern to copy
`features/calculations/` (trace tree for untyped objects, filters in the URL, `rebates-page.tsx` = tier tracker),
`features/claims/` (export job polling → `GET /files/{file_id}/download-url`). Shared: `components/ui.tsx`,
`masterdata/common.tsx` (`ManufacturerSelect`, pager, `DataTable`), `lib/api/*`, `lib/session.tsx`, `app/routes.tsx`.

## Allowed files
- **May edit:** `frontend/src/features/reporting/**` (new), `frontend/src/app/routes.tsx` (route lines) and nav entries in
  `app/app-layout.tsx`, `frontend/e2e/reporting-*.spec.ts` (new), `frontend/e2e/support.ts` (add helpers only),
  `docs/reports/notes/ui-report-s10.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, other `docs/`, `frontend/src/api/`,
  `package.json`/lockfile, other feature folders (the tier tracker stays in `calculations/`, so only link to it).

## Rules (CLAUDE.md §10)
No business calculations: no sums of drill-down rows, no month totals, no percentages, no "collected ÷ claimed", no
at-risk logic, no chart axes derived from your own maths beyond scaling the server values for display. Show server strings
exactly (grouping only). Use the generated client only. The server is the authority on permissions and the UI handles
403. Every screen has loading, empty (`as_of` null → "Figures have not been built yet"), error and permission-denied
states, keyboard use, labels and a clean axe check. No mocks.

## API (prefix `/api/v1`)
- `GET /dashboard` [`dashboard_get`] `manufacturer_id?, branch_id?, month?` (any day of the month) → `DashboardOut{as_of,
  as_of_date, figures{claimed, collected, short_paid, recovered, written_off, at_risk, expiring}, months[{month, figures}]}`. All roles.
- `GET /dashboard/drilldown` [`dashboard_drilldown`] `metric` (same 7) + filters + `limit` ≤ 200 + `cursor` →
  `{figure, total, consistent, as_of, data[{id, label, amount, month, manufacturer_id, branch_id, trace_kind, trace_id}], page}`.
- `POST /dashboard/refresh` [`dashboard_refresh`] (`report.refresh`: admin, approver, analyst) → 202 `{job_id}`.
  `null` means already queued this minute, so show "A refresh is already queued".
- `GET /trace/claim_line/{id}` [`trace_claim_line`], `/trace/remittance_line/{id}` [`trace_remittance_line`],
  `/trace/variance_case/{id}` [`trace_variance_case`], `/trace/calculation_result/{id}` [`trace_calculation_result`] →
  `{kind, id, chain[{kind, id, label, fields}]}`. 404 for another tenant's id.
- `GET /reports/claim-batches/{id}/audit-pack?format=xlsx` [`claim_batch_audit_pack`] (`audit_pack.export`: admin,
  approver, **viewer**; analyst gets 403) → 202 `{job, file_id}` → poll `GET /jobs/{id}` → download. **XLSX only**: do not offer CSV or PDF.
- `GET /rebate-tracker` (existing, S5) is already shown at `/calculations/rebates`.

## Screens
1. **Dashboard `/dashboard`** (also linked from home): filters (manufacturer, branch, `<input type="month">` → sends
   `YYYY-MM-01`) in the URL. Seven figure cards (plus the reserved slot), each a link to its drill-down, with "As of
   {as_of}; at-risk computed for {as_of_date}". "Refresh figures" behind `can("report.refresh")`. Monthly table
   (rows = server `months[]`, columns = the 7 figures; it may include an accessible bar per cell scaled from server
   values only, with the table as the accessible source of truth). Link "Rebate tier tracker" → `/calculations/rebates`.
2. **Drill-down `/dashboard/:metric`** (filters carried over): header shows the server's `figure` and `total`. If
   `consistent === false`, show a warning ("Data changed since the last refresh") with the Refresh action. Cursor-paged
   table (label, month, manufacturer, branch, amount). Each row links to `/trace/:trace_kind/:trace_id`.
3. **Trace `/trace/:kind/:id`**: an ordered chain (list, top = record, bottom = source file row). Each node shows its
   label and `fields` as a labelled key/value tree. A node whose kind is one of the 4 trace kinds links to its own trace.
   A rebate accrual node ends the chain with the note "Aggregates many purchases: no single source row". Unknown kind
   → 404 page.
4. **Audit pack `/reports/audit-pack`**: pick a manufacturer → a submitted batch (`GET /claim-batches?status=submitted`)
   → "Generate audit pack (XLSX)" → job progress → download link.

## Roles and a11y
All roles read the dashboard, drill-down and trace. Refresh is for admin, approver and analyst. The audit pack is for
admin, approver and viewer, and an analyst sees a permission notice. Cards are links with accessible names ("Claimed:
10,722.50, view details"). The chain is an `<ol>`. Tables have headers. Colour is never the only signal.

## Playwright (`e2e/reporting-*.spec.ts`, real stack)
Setup: reuse the S9 setup (submitted VOLT batch + CM-7781 finalised → cases), then `POST /dashboard/refresh` via the
button and wait for `as_of` to change.
1. `reporting-dashboard`: each card text equals the drill-down page's `figure` and `total` (all server strings), and
   `consistent` is true. Then assert the exact story values the merged backend report states for CB-VOLT-2026-06 + CM-7781
   under the owner definitions. The month filter changes the cards. An empty tenant shows "not built yet".
2. `reporting-trace`: drill-down row → trace → chain ends at the source file row (file name + row number shown).
3. `reporting-audit-pack`: approver generates XLSX → download link. An analyst sees the permission notice.
4. axe check on every page.
Selectors use role, label or name only, scoped to a card, row, region or dialog. Run at most 4 specs per batch. On 429,
stop and ask the owner to clear `rate_limit_counters`. Never loosen an assertion.

## Commands (from `frontend/`)
`npm run lint`, `npm run typecheck`, `npx vitest run src/features/reporting`, `npx playwright test e2e/reporting-*.spec.ts`.
At the end: `npm test`, then full Playwright in batches of ≤ 4.

## Gaps (record, do not work around)
No "recovered after write-off" field yet. The audit-pack 202 body is untyped (`{}`) in OpenAPI (as are claim-batch export and remittance reconcile), so
`job`/`file_id` have no generated type. The owner figure definitions (claimed incl. adjustments, a single at-risk) are
not yet in `m1-s10`. There is no PDF audit pack (Release 1 is XLSX only, by decision).
