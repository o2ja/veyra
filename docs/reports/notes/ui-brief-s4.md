# UI brief — S4 agreements frontend (self-contained)

**Goal.** Build the agreements screens of Veyra Recover (rebate/claim recovery SaaS, React 18 + TS + Vite +
React Router + TanStack Query + Tailwind; generated OpenAPI client). Users create agreement drafts, fix
validation errors, submit, and a *second* user approves them into immutable versions. All data is real (real
API, real Postgres). Data in tests is SYNTHETIC.

## Pattern to copy (read these, nothing else in the repo)
Feature folder `frontend/src/features/masterdata/`:
- page: `manufacturers-pages.tsx` (list + create + detail; `If-Match` save with 412 reload at line ~138)
- API hooks/helpers: `common.tsx` (`usePager`, `PagerControls`, `DataTable`, `SelectField`, `ManufacturerSelect`,
  `EntityPicker`); client `frontend/src/lib/api/client.ts` (`api.GET/POST/PUT`), `frontend/src/lib/api/errors.ts`
  (`unwrap`, `ApiError{status,code,details[]}`); UI kit `frontend/src/components/ui.tsx` (`Button, Alert,
  ErrorAlert, Card, Loading, Empty, PermissionDenied, Field, fieldError`); session `frontend/src/lib/session.tsx`
  (`useSession().can(perm)`, `me.user`)
- tests: e2e `frontend/e2e/masterdata.spec.ts` + `frontend/e2e/support.ts` (register, MFA, signIn, axe);
  unit test example `frontend/src/features/imports/logic.test.ts`
- routing: `frontend/src/app/routes.tsx` (`<RequirePermission permission="…">`), nav `frontend/src/app/app-layout.tsx`
- upload: `uploadWithProgress(file, kind, onProgress)` exported from `frontend/src/features/files/files-page.tsx`
- types: `frontend/src/api/schema.d.ts` → `components["schemas"]["<Name>"]` (names below)

## Folders
- **May edit:** `frontend/src/features/agreements/**` (new; an earlier `logic.ts` there may be replaced),
  `frontend/src/app/routes.tsx` and `app-layout.tsx` (route + nav lines only), `frontend/e2e/agreements*.spec.ts`,
  `docs/reports/notes/ui-report-s4.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, `docs/` (except the report),
  `frontend/src/api/` (generated), `package.json`/lockfile (no new dependencies), other feature folders.

## Rules (CLAUDE.md §10 + project rules)
1. Use the generated client and its types only; never hand-write API types (the draft payload types are
   `Payload, Header, SpaRules, RebateRules, Item, TierIn, CustomerRef, Rounding, Calculation, …`).
2. No business calculations in the UI: no rebate amounts, no validity/overlap/eligibility logic. Validation
   results come from the server (`validation`); display money/rates as the server's decimal strings.
3. The server is the authority on permissions; the UI only hides/disables. Always handle 403.
4. No secrets/tokens/sensitive data in code, `localStorage` or `console`.
5. Every screen handles loading, empty, error (`ErrorAlert`) and permission-denied (`PermissionDenied`);
   keyboard accessible, labelled controls, axe-clean (WCAG 2.1 AA).
Also: no mock data, no fake responses, no placeholder screens, no TODO stubs.

## API conventions
Prefix `/api/v1`. Errors: `ApiError.code` (e.g. `validation_error` 422 with `details[{field,code,message}]`,
`invalid_state` 409, `four_eyes_violation` 403, `precondition_failed` 412, `not_found` 404). Lists:
`{data, page:{has_more,next_cursor}}`, query `limit` (≤200) + `cursor`. `If-Match: "<revision>"` (quoted
integer from `DraftOut.revision`); 412 → show "changed by someone else" + **Reload** button. `Idempotency-Key`:
a fresh `crypto.randomUUID()` per user action (reuse it on retry of the same click).
Permissions: reads `data.read`; create/edit/validate/import/submit `agreement.draft`;
approve/reject/return/cancel `agreement.approve`.
Draft statuses: `draft, pending_approval, approved, rejected, needs_manual_entry, cancelled`.
Version statuses: `active, superseded, expired, cancelled`.

## Screens

### 1. Agreements list — `/agreements` (data.read)
`GET /agreements?manufacturer_id&agreement_type(spa|rebate)&limit&cursor` → `AgreementList`
(`AgreementOut`: id, external_number, name, agreement_type, manufacturer_id, cancelled_at,
current_version: `VersionSummary|null`). Filters (manufacturer via `ManufacturerSelect`, type), keyset pager,
row → detail. Button "New draft" (agreement.draft) → screen 4.
Accept: filters change the request; empty state text; cancelled agreements marked; axe clean.

### 2. Agreement detail — `/agreements/:agreementId` (data.read)
`GET /agreements/{id}` → `AgreementOut`; `GET /agreements/{id}/versions?limit&cursor` → `VersionList`
(`VersionSummary`: id, version_number, status, valid_from, valid_to, supersedes_version_id,
derived_from_version_id, activated_at, approved_by). Version history table, newest first as returned.
Actions: "Amend version" on an active/expired version (agreement.draft) → `POST /agreement-drafts`
`{source:"manual", agreement_id, amends_version_id, payload: <version payload, see below>}` → editor;
"Cancel agreement" (agreement.approve) → dialog `POST /agreements/{id}/cancel` `CancelIn{effective_date
(YYYY-MM-DD), reason}` → refresh. Compare two versions → screen 3 diff.
To prefill an amendment payload, build it from `GET /agreement-versions/{id}` fields (header from
external_number/name/agreement_type/priority/valid_from/valid_to + manufacturer code from master data, `rules`
as returned, customers, tiers, items from screen 3's items endpoint) — copying, no computation.
Accept: 404 → not-found message; cancel 422 shows field errors; buttons hidden without permission.

### 3. Version detail + diff — `/agreements/versions/:versionId`, `/agreements/versions/:id/diff/:otherId` (data.read)
`GET /agreement-versions/{id}` → `VersionOut` (header fields, status, rules object, customers[{customer_code,
scope}], tiers[`TierOut`], item_count, content_hash, supersedes/superseded_by/derived_from links).
Items: `GET /agreement-versions/{id}/items?limit&cursor` → `ItemList` (`ItemOut`: line_number, sku,
reference_cost, deviated_cost, sell_price, rate, flat_amount, max_quantity, claim_uom) — server-paged table.
Rules shown read-only as labelled key/value list. Diff: `GET /agreement-versions/{id}/diff/{otherId}` →
`DiffOut{changes[{path, before, after}]}`; render a table path / before / after; empty → "No differences".
Accept: links between superseded/successor versions work; 20k-item version pages without freezing.

### 4. Drafts list — `/agreement-drafts` (data.read)
`GET /agreement-drafts?status&agreement_id&limit&cursor` → `DraftList` (`DraftSummary`). Status filter
(default `pending_approval` for users with agreement.approve, else `draft`). "New draft" → `POST
/agreement-drafts` `DraftCreate{source:"manual", payload?}` (201 `DraftOut`) → editor.
Accept: filter in URL query; empty state; row → editor.

### 5. Draft editor + validation — `/agreement-drafts/:draftId` (data.read; editing agreement.draft)
`GET /agreement-drafts/{id}` → `DraftOut` (payload, revision, status, validation, created_by, review_comment,
rejection_reason, resulting_version_id). Editable only when `status=draft` and user can agreement.draft.
- Sections: Header (`Header`), Rules (form switches on `rules.type`: `SpaRules` or `RebateRules`; enum fields
  as selects with the enum values from the schema), Customers (`CustomerRef[]`), Items (`Item[]`, up to 20 000:
  show 100 rows per page client-side; no virtualisation library is installed and none may be added),
  Tiers (`TierIn[]`, rebate only). Decimal fields are text inputs holding strings; no arithmetic.
- Save: `PUT /agreement-drafts/{id}` header `if-match` + `DraftUpdate{payload, change_summary?}` → 200
  `DraftOut` (saved even if invalid). 412 → reload path.
- Validation panel from `DraftOut.validation` (`Validation`: valid, error_count, errors[`Issue{field,code,
  message,id}`], warnings[`Issue`], references{unresolved_customers[], unresolved_skus[]}). Errors and warnings
  visually distinct; clicking an issue focuses its field when the path maps (`payload.items[3].sku`). "Validate"
  → `POST /agreement-drafts/{id}/validate`.
- Bulk items import: upload CSV/XLSX with `uploadWithProgress(file, "spreadsheet", …)` → `POST
  /agreement-drafts/{id}/items:import` header `if-match`, `ItemsImportIn{file_id, rate_format(fraction|percent),
  dry_run, sheet?, header_row?, mapping?}` → `ItemsImportOut{rows, items[], dry_run, draft}`; dry run preview
  first, then real import replaces the editor state with `draft`.
- Submit (agreement.draft): `POST …/submit` `SubmitIn{acknowledged_warnings: [issue.id…]}`; every warning
  needs a checkbox; 422 `unacknowledged_warning` / `validation_error` shown.
- Approver actions when `status=pending_approval` (agreement.approve): Approve → `POST …/approve` headers
  `idempotency-key` + `if-match`; Reject → `RejectIn{reason}`; Return → `ReturnIn{comment}` (back to `draft`,
  `review_comment` shown on the editor). Approve is disabled with an explanation when `created_by ===
  me.user.id` (four-eyes); still handle 403 `four_eyes_violation` (editors of a revision also count).
  After approval show a link to `resulting_version_id` (screen 3).
Accept: unsaved-changes guard; 412/409/403/422 each have a visible message; read-only view for other states.

## Verify (run as you go and all at the end; from `frontend/`)
- `npm run lint` · `npm run typecheck` · `npx vitest run src/features/agreements` (then `npm test`)
- E2E against the real stack (owner keeps Docker running: API on `localhost:8000`, Mailpit `localhost:8025`):
  `docker compose --env-file .env -f infra/docker/compose.dev.yml stop frontend` (from the repo root, frees
  port 5173), then in `frontend/`: `npm run dev` (separate terminal) and
  `$env:PW_CHANNEL="chrome"; npx playwright test e2e/agreements.spec.ts` (then `npx playwright test`).
- E2E required: W5/W7 manual path — admin registers + MFA (copy masterdata.spec), creates manufacturer VOLT
  and the needed customers/products via the UI, invites an approver (copy invite steps from
  `e2e/w1-w2.spec.ts` lines ~40–70), creates a draft by filling the form from
  `testdata/generated/agreements/manual_payloads/SPA-VOLT-2026-884.json` and `REB-VOLT-2026.json` (read these two
  files only), sees and fixes one validation error, submits, author's Approve is disabled, approver approves,
  version visible with correct item count; one amendment → history shows superseded + new version; diff
  shows the changed field; axe on every new page. Unit tests for any non-trivial pure logic (payload form ↔
  object mapping, issue-path → field mapping).

## If an endpoint is missing or differs
Do not fake, mock, or work around it in the UI. Record it in the report (endpoint, expected vs actual, screen
affected), build the rest, and mark that part not done.
