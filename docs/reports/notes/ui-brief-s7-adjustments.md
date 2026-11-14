# UI brief: S7 adjustments queue and batch totals (branch `ui/s7-adjustments`, self-contained)

**Goal.** Finish the S7 claim UI. The claim screens already exist (`ui/s7`: missed claims, batches, batch detail,
export templates). What is missing is the adjustments queue, putting approved adjustments (and rebate accrual entries)
into a batch, and showing a batch's carried adjustments. Everything uses the real API. The server computes every amount.

**Base.** Branch `ui/s7-adjustments` already exists in the worktree `D:/veyra/veyra-ui-s7-adjustments`: backend branch s7-claims (S7 final, 5th financial review MERGEABLE), with `ui/s7` merged in. It is not on main yet; the orchestrator merges the backend and this branch later. Do not merge, rebase or wait for main.

## Pattern to copy
- `frontend/src/features/claims/` (from `ui/s7`): `claim-batch-detail-page.tsx` (dialogs, `canApproveBatch` four-eyes
  gating with `useSession().me.user.id`), `missed-claims-page.tsx` (batch builder), `logic.ts` + `logic.test.ts`.
- shared: `components/ui.tsx`, `features/masterdata/common.tsx` (`usePager, PagerControls, DataTable, SelectField,
  ManufacturerSelect`), `lib/api/client.ts` + `errors.ts`, `lib/session.tsx`, `app/routes.tsx` (`guarded`).
- types: `frontend/src/api/schema.d.ts` → `components["schemas"]["AdjustmentOut" | "BatchIn" | "BatchOut" | "LineOut"]`.

## Allowed files
- **May edit:** `frontend/src/features/claims/**`, `frontend/src/app/routes.tsx` (route lines) and its nav entry in
  `frontend/src/app/app-layout.tsx`, `frontend/e2e/claims-*.spec.ts` (new) and `frontend/e2e/claims.spec.ts` (fixes
  only, never loosen an assertion), `frontend/e2e/support.ts` (add helpers only), `docs/reports/notes/ui-report-s7-adjustments.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, other `docs/`, `frontend/src/api/`
  (generated), `package.json`/lockfile, other feature folders (incl. `calculations/`, which already has "Propose adjustments").

## Rules (CLAUDE.md §10)
1. Generated client and types only. 2. **No business calculations**: no sums, subtotals, sign logic, "net" amounts or
"can this be net-claimed" checks. Show the server's signed decimal strings exactly (grouping only). 3. The server is
the authority on permissions. The UI hides or disables only, and handles 403. 4. No secrets or PII in storage or the console.
5. Every screen has loading, empty, error and permission-denied states, keyboard use, labels and a clean axe check.
No mocks, no placeholders, no TODO stubs.

## API (prefix `/api/v1`, operationId in brackets)
- `GET /claim-adjustments` [`claim_adjustment_list`] filters `status` (`proposed|approved|included|rejected`),
  `manufacturer_id`, `adjustment_type` (`return_reversal|agreement_change|manual_correction|rebate_clawback|
  recovery_reclaim`), `claim_line_id`, `included_in_batch_id`, `limit`, `cursor` → `AdjustmentList` (newest first;
  rows carry `claim_reference`, `amount` (signed), `reason`, `proposed_by`, `approved_by/at`, `included_in_batch_id`, `run_id`).
- `POST /claim-adjustments/{id}/approve` [`claim_adjustment_approve`] (`adjustment.approve`: admin, approver). Only
  from `proposed`. 403 `four_eyes_violation` for the proposer; 409 `cannot_net_claim` (details carry `adjustment_id`
  and the message "Discuss with the manufacturer…"); 409 `invalid_state`.
- `POST /claim-adjustments/{id}/reject` [`claim_adjustment_reject`] (`adjustment.approve`). Allowed from
  `proposed|approved`, no body. 409 `invalid_state` otherwise.
- `POST /claim-batches` [`claim_batch_create`] `BatchIn{manufacturer_id, include_adjustment_ids[], one of result_ids |
  filter | accrual_entry_ids}` + `Idempotency-Key`. Errors to show per item: 409 `adjustment_already_included`
  (`adjustment_id`, `batch_id` → link), 409 `cannot_net_claim`, 422 `batch_total_not_positive`, 422 details
  `not_approved`, `wrong_manufacturer`, `not_found`, `not_claimable` (accrual delta ≤ 0), 409 `already_claimed`.
- `GET /claim-batches/{id}` [`claim_batch_get`]: `total_amount` = lines + signed adjustments (server). `line_count`
  counts lines only. `GET /claim-batches/{id}/lines` [`claim_batch_line_list`]: `source_kind` (`sales_line|rebate_accrual`).
- `GET /rebate-accruals?agreement_id&run_id` (existing S5 op) for accrual entry selection.

## Screens
### 1. Adjustments queue: `/claims/adjustments` (data.read)
Status tabs (Proposed / Approved / Included / Rejected) and type + manufacturer filters, all kept in the URL. Cursor pager.
Columns: claim reference (link to the batch line when one exists), type (plain words), signed amount (right-aligned,
server string, "−" kept), reason, run, proposed by/at, status, included batch (link). Row actions behind
`can("adjustment.approve")`, each in a confirm dialog: **Approve** (disabled with an explanation when
`proposed_by === me.user.id`; the server still decides) and **Reject**.
**cannot_net_claim:** the row has no flag field (see gap). On 409 `cannot_net_claim`, keep the server message on that
row as a warning badge ("Cannot be net-claimed: discuss with the manufacturer"; text plus an icon, never colour
alone). Do not infer it from type and sign.
### 2. Include in batch: in the batch builder (`missed-claims-page.tsx`)
When a manufacturer is chosen, an "Adjustments to carry" section lists `status=approved&manufacturer_id=X` with
checkboxes. Selected ids go to `include_adjustment_ids`. No client total. After 201, show the server's `total_amount`.
Add a "Claim rebate accruals" mode: select entries from `GET /rebate-accruals`, send `accrual_entry_ids`, and show
each per-entry `not_claimable` error.
### 3. Batch detail additions (`claim-batch-detail-page.tsx`)
- "Carried adjustments" table: `GET /claim-adjustments?included_in_batch_id={id}` (type, reference, signed amount, reason).
- Lines table gets a "Source" column (`source_kind`: "Sales line" / "Rebate accrual").
- Totals block: `total_amount` with the label "Batch total (lines + carried adjustments, from server)" and
  `line_count`. **No** lines subtotal and no adjustments subtotal, because the server has neither (see gap).
- Voiding a draft/approved batch frees its adjustments (they become `approved` again). After a void, refetch the queue.
### 4. Export template sources
Add `line_type` (text; values `claim`, `rebate_claim`, `adjustment:<type>`) to the source list in `logic.ts`.

## States, roles, a11y
Viewer and analyst see the queue read-only (no Approve/Reject). A 403 on any action shows the server message. Empty
tab: "No proposed adjustments". Errors show `ApiError.message` + `details[]` per item. Dialogs trap focus and close
on Escape. Tabs use Radix Tabs (`role=tab`). Run an axe check on the queue and the batch detail.

## Playwright (`e2e/claims-adjustments.spec.ts`, real stack)
Setup: reuse the `claims.spec.ts` steps (tenant, approved SPA, import, completed run, submitted batch), then import
the return/changed sales file the backend S7 tests use and run again. On the run detail, click "Propose adjustments".
1. Analyst sees the proposals under Proposed (row scoped by `getByRole("row", {name: /<claim_reference>/})`) with no
   Approve button. The approver who proposed sees Approve disabled. A second approver approves.
2. Reject one, and it appears under Rejected.
3. Approved adjustment → missed-claims builder → include → new batch detail shows it under "Carried adjustments"
   and the total equals the server string for that batch. Compare `GET` text with the page text, with no arithmetic in the test.
4. Including the same adjustment again shows `adjustment_already_included` with a link to the first batch.
5. For a manufacturer without `allow_negative_claims` (set it on the manufacturer settings page), approving a
   negative return reversal shows the `cannot_net_claim` message.
6. axe check on each page. Selectors use only role, label or name, scoped to a row, dialog or region. No CSS or test ids.
Run specs in **batches of at most 4**. If you hit a 429 `rate_limited`, stop and ask the owner to clear
`rate_limit_counters`. Never disable limits, never add retries that hide failures, and never weaken an assertion.

## Commands (from `frontend/`)
While building: `npm run lint`, `npm run typecheck`, `npx vitest run src/features/claims`,
`npx playwright test e2e/claims.spec.ts e2e/claims-adjustments.spec.ts`. At the end: `npm test`, then full Playwright in
batches of ≤ 4 specs. `make ci-local` runs at merge (owner/orchestrator), not by you.

## Gaps (record in the report, do not work around)
`AdjustmentOut` has no `cannot_net_claim` flag. `BatchOut` has no lines/adjustments subtotal. Reject takes no reason.
