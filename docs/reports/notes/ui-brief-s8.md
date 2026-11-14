# UI brief: S8 reconciliation workbench (branch `ui/s8`, self-contained)

**Goal.** Build the remittance and reconciliation screens: upload a remittance, review and accept it, run matching,
confirm or reject T1–T5 suggestions, match by hand, categorise unmatched lines, finalise, and show the findings. Everything
uses the real API. The server decides every match, amount, variance and reason.

**Base.** Branch `ui/s8` already exists in the worktree `D:/veyra/veyra-ui-s8`: backend branch s8-reconciliation (S8 under its last review; API may still get small fixes), with `ui/s7` merged in. It is not on main yet; the orchestrator merges the backend and this branch later. Do not merge, rebase or wait for main.

## Pattern to copy
`frontend/src/features/claims/` (dialogs, `Idempotency-Key`, job polling, four-eyes gating) and
`features/calculations/` (run polling, filters in the URL, generic key/value tree for untyped objects). Shared:
`components/ui.tsx`, `features/masterdata/common.tsx`, `features/files/files-page.tsx` (`uploadWithProgress(file,
"remittance", …)`), `lib/api/*`, `lib/session.tsx`, `app/routes.tsx` (`guarded`). Types: `components["schemas"][…]`.

## Allowed files
- **May edit:** `frontend/src/features/reconciliation/**` (new), `frontend/src/app/routes.tsx` (route lines) and its
  nav entry in `app/app-layout.tsx`, `frontend/e2e/reconciliation-*.spec.ts` (new), `frontend/e2e/support.ts`
  (add helpers only), `docs/reports/notes/ui-report-s8.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, other `docs/`, `frontend/src/api/`,
  `package.json`/lockfile, other feature folders.

## Rules (CLAUDE.md §10)
No business calculations: no sums of allocations, no "remaining to allocate", no difference claimed − paid, no total
check, no tier logic. Show server strings exactly (grouping only). Match `evidence` is untyped, so render it as a labelled
key/value tree and never re-type it. Use the generated client only. The server is the authority on permissions and
the UI handles 403. Every screen has loading, empty, error and permission-denied states, keyboard use, labels and a
clean axe check. No mocks or stubs.

## API (prefix `/api/v1`; permission `remittance.reconcile` = admin, approver, analyst; reads `data.read`)
- `POST /remittances` [`remittance_create`] `RemittanceIn{file_id, manufacturer_id, doc_type, channel,
  mapping_template_id?, document_number?, document_date?, payment_date?, total_amount?, currency_code?}` +
  `Idempotency-Key` → 202 `{remittance, job}`. Errors: 409 `ai_not_enabled` (PDF), `duplicate_remittance`, `conflict` (file reused).
- `GET /remittances` [`remittance_list`] (`status`, `manufacturer_id`, paged), `GET /remittances/{id}` [`remittance_get`]
  (statuses `uploaded, pending_review, accepted, reconciled, void`), `GET /remittances/{id}/lines` [`remittance_line_list`].
- `GET /remittances/{id}/extraction` [`remittance_extraction_get`] (**needs `remittance.reconcile`**: a viewer gets
  403, so show "Review is limited to reconcilers") → staged `lines[]` with `issues[]` and document `problems[]`.
- `POST /remittances/{id}/accept` [`remittance_accept`] `RemittanceAcceptIn{document_number?, document_date?,
  payment_date?, total_amount?, corrections[LineCorrection]}`. 422 `total_required`, 409 `cannot_accept`
  (details = remaining problems). `POST /remittances/{id}/void` [`remittance_void`] (uploaded/pending_review only).
- `POST /remittances/{id}/reconcile` [`remittance_reconcile`] → 202 job; poll `GET /jobs/{id}`. Re-runs keep confirmed matches.
- `GET /remittances/{id}/reconciliation` [`remittance_reconciliation_get`] → per line `state`
  (`matched|suggested|unmatched`), `line`, `matches[{method, score, status, revision, evidence, allocations[]}]`.
- `POST /matches/{id}/confirm|reject` [`match_confirm` / `match_reject`] with `If-Match: "<revision>"` (412 → reload
  banner; 428 is a UI bug). `GET /matches?remittance_id&status` [`match_list`].
- `POST /matches` [`match_create`] `ManualMatchIn{remittance_line_id, claim_line_id | claim_adjustment_id, allocated_amount}`.
- `POST /remittance-lines/{id}/categorise` [`remittance_line_categorise`] `{category:"unmatched_payment"}`
  (409 `line_matched`, `invalid_state`).
- `POST /remittances/{id}/finalize` [`remittance_finalize`] + `Idempotency-Key` → `FinaliseOut{remittance, findings[]}`.
  409 `unresolved_suggestions`, 409 `uncategorised_lines` (list the lines from `details`).
- `GET|PUT /manufacturers/{id}/reason-code-mappings` [`reason_code_mapping_list` / `_replace`] (write: `masterdata.write`).

## Screens
1. **Remittances `/reconciliation`**: status and manufacturer filters in the URL, plus a cursor pager. "New remittance"
   button behind `can("remittance.reconcile")`.
2. **New `/reconciliation/new`**: upload (CSV/XLSX/PDF), then a form (RHF + Zod checks format only: required ids,
   date inputs, decimal string pattern) and an optional mapping template. Poll the job, then open the detail page.
3. **Detail `/reconciliation/:remittanceId`**, with a header showing status, channel, doc type, totals and dates.
   - `pending_review`: staged lines grid with per-line issues and problems (codes as plain words with the server
     message). Editable corrections per line and the document fields. `total_required` gets its own **required**
     "Document total" field and an explanation. Accept and Void are in confirm dialogs.
   - `accepted`: workbench. A side-by-side table of remittance line and its matches, with filter chips by state. For
     each suggestion: method shown as T1–T5 with words (exact reference, invoice line, composite, fuzzy, subset sum),
     score, allocations (claim reference + server amount), evidence tree, and Confirm / Reject. For an unmatched line:
     **Manual match** dialog (pick a submitted batch of the manufacturer → its lines, or a carried adjustment via
     `GET /claim-adjustments?included_in_batch_id`, then an allocated amount; hint: "0.00 only when the line paid 0.00,
     which marks the claim line rejected". Do not pre-check it, show the server's 422) and **Mark as unmatched payment**.
     Then "Run matching" and **Finalise** (dialog says the result is immutable).
   - `reconciled`: read-only. Show findings from the finalise response (`case_type`, `reason_code`, claimed, paid,
     amount, claim reference), each linking to S9 when it exists. After a reload findings are not retrievable (gap).
4. **Reason code mappings** on `/reconciliation/reason-codes`: per manufacturer, an editable table of external
   code to controlled `reason_code` (a select from the generated enum).

## Roles and a11y
A viewer reads lists, detail and reconciliation, with no actions and no staging view. Analyst, approver and admin
get all actions. Tables have headers, state chips are text and not colour only, dialogs trap focus, and Escape closes them.

## Playwright (`e2e/reconciliation-*.spec.ts`, real stack; ground truth `testdata/generated/ground_truth/expected_reconciliation.json`)
Setup: reuse the claims specs to get a **submitted** VOLT batch. Then upload `remittance/credit_memo_VOLT_CM-7781.csv`.
1. `reconciliation-ingest`: list → new → accepted (or `pending_review` with `total_required`: enter the total → accept).
   A duplicate document number shows `duplicate_remittance`. A viewer gets no New button and a 403 notice on staging.
2. `reconciliation-workbench`: run matching. Suggestions are shown with method and evidence. Confirm one (row scoped)
   and reject one. A stale revision (second tab) gives 412 and the banner. A manual match to a claim line works.
3. `reconciliation-finalise`: finalise early → `uncategorised_lines` lists the lines → categorise → finalise →
   findings equal the ground-truth strings (short_paid 150.00, rejected 2080.00, unmatched_payment 75.00, unpaid
   660.00). The remittance is then read-only.
4. axe check on every page.
Selectors use role, label or name only, scoped (`getByRole("row",{name})`, `getByRole("dialog")`). Run at most 4 specs
per batch. On 429, stop and ask the owner to clear `rate_limit_counters`. Never loosen an assertion. PDF ingest needs
live AI (not available), so only check the `ai_not_enabled` message.

## Commands (from `frontend/`)
`npm run lint`, `npm run typecheck`, `npx vitest run src/features/reconciliation`,
`npx playwright test e2e/reconciliation-*.spec.ts`. At the end: `npm test`, then full Playwright in batches of ≤ 4.

## Gaps (record, do not work around)
No endpoint lists the findings of a reconciled remittance (and `GET /cases` has no `remittance_document_id` filter).
No claim-line search by reference for a manual match (you have to go batch by batch). `claim_adjustment_id` matching is interim (D-N11).
