# UI brief — S7 claims frontend (self-contained)

**Goal.** Add the S7 claim screens to Veyra Recover: missed-claims scan, claim batch builder, batch list/detail
(approve, export, submit, void), and claim export templates. Everything talks to the real API; the server computes
every amount and every rule. The adjustments queue is **out of scope** (its API lands later, S7.4).

## Pattern to copy (read these, nothing else in the repo)
- `frontend/src/features/calculations/` (runs list, run detail, filters, pager, polling a job) and
  `frontend/src/features/agreements/` (`draft-editor-page.tsx`: `Idempotency-Key`, `If-Match`/412 reload,
  four-eyes disabled Approve, read-only states; `logic.ts` + `logic.test.ts` for pure helpers).
- shared: `frontend/src/components/ui.tsx`, `frontend/src/features/masterdata/common.tsx` (`usePager, PagerControls,
  DataTable, SelectField, ManufacturerSelect`), `frontend/src/lib/api/client.ts` + `errors.ts`, `lib/session.tsx`
  (`useSession().can(perm)`), `app/routes.tsx` (`guarded`), `app/app-layout.tsx` (nav), `features/files/files-page.tsx`
  (download via `download-url`).
- e2e: `frontend/e2e/calculations.spec.ts` (builds a tenant with imported sales + approved SPA + completed run: reuse
  its setup steps) and `frontend/e2e/support.ts` (keep importing it: per-test client IP).
- types: `frontend/src/api/schema.d.ts` → `components["schemas"]["<Name>"]`; search by name, never read it whole.

## Folders
- **May edit:** `frontend/src/features/claims/**` (new), route + nav lines in `routes.tsx`/`app-layout.tsx`,
  `frontend/e2e/claims*.spec.ts` (new), `docs/reports/notes/ui-report-s7.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `docs/` (except your report),
  `frontend/src/api/` (generated; in sync with the API at the branch base), `package.json`/lockfile, other features.
- Branch `ui/s7`, worktree `D:\veyra\veyra-ui-s7` (base: S7 claims API + S5/S6 UI).

## Rules
1. Generated client and types only. 2. **No business calculations**: no sums, totals, selection totals, deadline
or eligibility logic; show server decimal strings exactly (grouping only). The batch total is the server's
`total_amount` after creation, never a client sum of the selection. 3. Server is the authority on permissions; UI
hides/disables only; handle 403. 4. No secrets/PII in `localStorage`/`console`. 5. Loading, empty, error,
permission-denied states; keyboard accessible, labelled, axe-clean. No mocks, placeholders or TODO stubs.

## API (prefix `/api/v1`)
Lists: `{data, page:{has_more, next_cursor}}`, `limit` ≤ 200 + `cursor`. `Idempotency-Key`: `crypto.randomUUID()`
per user action, reused on retry of the same click. Permissions (SECURITY §3): reads `data.read` (all roles);
`claim_batch.create` (create batches, export, manage templates: admin, approver, analyst);
`claim_batch.approve/submit/void` (admin, approver). Four-eyes: when the tenant setting is on, the batch creator
cannot approve (server returns 403/409 — show the message; disable Approve for the creator as the agreements
editor does). Errors: `ApiError.code` + `details[]` (`validation_error` 422 with per-item `code`:
`stale_result`, `not_eligible`, `wrong_manufacturer`, `already_claimed` (409 conflict, details list the conflicting
lines), `no_lines`, `too_many_lines`; `invalid_state` 409; `precondition_failed` 412). Show messages verbatim and
list `details` per line where given.

## Screens
### 1. Missed claims — `/claims/missed` (data.read)
- `GET /missed-claims/summary?manufacturer_id&agreement_id&invoice_from&invoice_to` → `MissedSummary`: totals,
  groups (manufacturer, agreement, lines, amount), exceptions by code (lines, stored_amount), lost_to_expiry.
  Show as cards + table exactly as given.
- `GET /missed-claims` (same filters, paged) → `MissedList` (`MissedOut`: document/line, invoice date, quantity,
  UoM, unit amount, claim amount, flags). Row checkboxes (select within one manufacturer only: the filter must
  name a manufacturer before selection is enabled).
- "Export list" (data.read): `GET /missed-claims?format=csv|xlsx&<filters>` → 202 `ExportAccepted{job, file_id}`;
  poll `GET /jobs/{job_id}` until done/failed, then offer download via `GET /files/{file_id}/download-url`.
- "Create batch" (claim_batch.create): either selected rows → `POST /claim-batches {manufacturer_id, result_ids}`
  or "all matching the filter" → `{manufacturer_id, filter:{agreement_id?, invoice_from?, invoice_to?}}`, with
  `Idempotency-Key`. On 201 navigate to the batch. On 422/409 show the per-line details (which line, which code).

### 2. Batches — `/claims/batches` (data.read)
`GET /claim-batches?status&manufacturer_id` → `BatchList`: number, manufacturer, status
(`draft, approved, submitted, void`), period, line_count, total_amount, created/approved/submitted info.

### 3. Batch detail — `/claims/batches/:batchId` (data.read)
`GET /claim-batches/{id}` (`BatchOut`) + `GET /claim-batches/{id}/lines` (paged `LineList`: claim_reference,
document/line, invoice date, quantity, unit amount, claim amount, frozen_at, released_at). Actions by status, each
behind its permission, each with a confirm dialog and `Idempotency-Key` where the API requires it:
- draft: **Approve** `POST …/approve` (four-eyes as above); **Void** `POST …/void {reason}` (reason required).
- approved: **Export** `POST …/export {template_id?}` (template picker: active templates of the batch's
  manufacturer; none = server default) → 202 job + file_id → poll → download link; **Submit** (dialog below);
  **Void**.
- submitted: read-only; shows submitted_at, external_reference, exported file download. Frozen lines are labelled.
- **Submit dialog**: `submitted_at` (`<input type="date">`; server refuses future dates — show `in_future`),
  `external_reference` (manufacturer portal reference, required), confirm text stating that submission freezes
  the lines; `POST …/submit` with `Idempotency-Key`. On success show the frozen state.

### 4. Export templates — `/claims/templates` (claim_batch.create)
`GET /claim-export-templates?manufacturer_id` → list; create `POST /claim-export-templates`
(`ExportTemplateCreate`: manufacturer_id, name, format `csv|xlsx`, columns[{header, source | constant, format?}],
options{include_header, delimiter `, ; \t |` (csv), sheet_name (xlsx)}, active); edit `PUT
/claim-export-templates/{id}` with `If-Match: "<revision>"` (412 → reload banner). Column editor: add/remove/
reorder rows; `source` is a select of these server field names (the API has no field-list endpoint; the server
validates, so a list drift shows as a 422): text `claim_reference, batch_number, manufacturer_code,
manufacturer_name, agreement_number, document_number, sku, product_description, manufacturer_part_number,
customer_code, customer_name, spa_reference, project_reference, claim_uom, currency_code`; int `line_number`; date
`invoice_date, ship_date`; decimal `quantity, unit_amount, claim_amount, unit_price, extended_amount`. Or a
`constant` instead of a source. `format` per kind: dates `iso|us|eu`, decimals `decimal:0..6` (the server refuses
`decimal:0/1` on money fields and fails an export whose format would change a value). Show 422 `details` next to the offending column.

## Acceptance
- All four screens work against the real API, with loading/empty/error/403 states, axe-clean.
- e2e `frontend/e2e/claims.spec.ts`: from the calculations-spec setup (completed run with eligible SPA lines),
  scan → summary values shown → select lines → create batch → batch total equals the summary amount shown for those
  lines (compare the server's strings, no client maths) → creator's Approve disabled → approver approves → create a
  CSV template → export with it → download link appears → submit with a reference → lines show frozen → the scan
  no longer lists them; a double-claim attempt shows `already_claimed` details; axe on each screen.
- Unit tests (`vitest`) for pure helpers you add (e.g. status → allowed actions, format options per field kind).

## Commands (from `frontend/`)
`npm run lint`, `npm run typecheck`, `npx vitest run src/features/claims`, then `npm test`; Playwright:
`npx playwright test e2e/claims.spec.ts`, then `npx playwright test`. The dev stack must be up; if you cannot run
Playwright in your sandbox, say "not run" in the report.
