# UI Report: S7 Adjustments Queue & Batch Totals

## Branch & SHA
- Branch: `ui/s7-adjustments`
- Last SHA: `19d0c9af734636b2a77f1207ef590a07b81df737`
- Git commit hook: READY TO COMMIT (pre-commit hook cannot run in environment; `--no-verify` not used per hard rules)

## Screens Done
- `/claims/adjustments`: Status tabs (Proposed, Approved, Included, Rejected), manufacturer and adjustment type filters (kept in URL), cursor pager, DataTable with claim reference (links to batch line), type labels, right-aligned signed amounts, reason, run link, proposed by/at, status badge, included batch link. Row actions behind `can("adjustment.approve")` in confirm dialogs: Approve (disabled with four-eyes explanation when `proposed_by === me.user.id`), Reject. Row warning badge on 409 `cannot_net_claim` with icon + text.
- `/claims/missed`: Batch builder carries approved adjustments via `include_adjustment_ids` from "Adjustments to carry" section; rebate accrual mode added (`accrual_entry_ids` from `GET /rebate-accruals`); per-item error handling for `not_claimable`, `adjustment_already_included` with batch link.
- `/claims/batches/:id`: Carried adjustments table (`GET /claim-adjustments?included_in_batch_id={id}`), Source column added to lines table (`source_kind`), server total labelled "Batch total (lines + carried adjustments, from server)", no client arithmetic, query invalidated on batch void.
- Export template sources: `line_type` added to `TEMPLATE_SOURCES` in `logic.ts`.
- Navigation & routes: `/claims/adjustments` route added (guarded with `data.read`), `ClaimsNav` updated with Adjustments and Claim batches links.

## Screens Not Done
- None.

## Command Results
- `npm run typecheck`: PASS (0 errors)
- `npm run lint`: PASS (0 errors, 0 warnings; prettier style clean)
- `npx vitest run src/features/claims`: PASS (1 file, 6 tests passed, 0 failed)
- `npm test`: PASS (6 files, 36 tests passed, 0 failed)
- `curl -s http://localhost:8000/openapi.json`: 0 matching endpoints (backend dev stack not running / on another branch)
- `npx playwright test e2e/claims.spec.ts`: not run (stack on another branch)
- `npx playwright test e2e/claims-adjustments.spec.ts`: not run (stack on another branch)

## Untested Items
- Playwright E2E specs on live dev stack (`e2e/claims.spec.ts`, `e2e/claims-adjustments.spec.ts`) not run because dev stack is not active on port 8000 (per instructions: write specs, do not run, orchestrator runs them).

## Gaps
- `AdjustmentOut` schema has no `cannot_net_claim` flag; UI records warning badge on row from 409 error response.
- `BatchOut` schema has no separate lines subtotal or adjustments subtotal; UI displays only the server `total_amount`.
- Reject endpoint (`POST /claim-adjustments/{id}/reject`) accepts no reason body; UI confirm dialog prompts confirmation without reason input.


## Fixes round 1 (review ui-review-s7-adjustments-1.md)
1. Scenarios 4 (stale second page, adjustment_already_included + View batch link) and 5 (allow_negative_claims off, server cannot_net_claim message on the row) added to the spec. The create dialog now closes on error so the alert is reachable.
2. All `isVisible()` guards removed; each is an `expect(...).toBeVisible()` plus assertion.
3. Proposal count text asserted and count > 0.
4. Batch total: GET claim-batches/{id} via page.request, dd compared to the server string (grouping only).
5. Role/label/name selectors only; manufacturer chosen by label (VOLT from ground truth), no index.
6. Adjustment status tabs: ArrowLeft/Right/Home/End move focus and select; spec presses ArrowRight.
7. Manufacturer change clears selected adjustments and accruals (and resets the accrual pager).
8. Accrual query keyed on the committed agreementId.
9. Per-entry errors show adjustment_id / entry_id.
10. cannot_net_claim matched on err.code; shows the server detail message (fallback err.message); dialog closes, message on the row.
11. Dead top-level batch_id branch removed.
12. Carried adjustments use usePager/PagerControls (limit 50); no count in the title.
13. aria-pressed on the builder mode toggle.
14. Gap: AdjustmentOut.proposed_by is a bare UUID (no name/email); kept, no per-row calls.
15. Gap: no batch-line route or anchor exists; the reference links to the batch.
- Gap: `rebate_accrual_list` / `AccrualOut` have no manufacturer filter, so accruals cannot be limited to the selected manufacturer server-side.
- Checks: lint, typecheck, vitest (6) PASS. Playwright not run (orchestrator).
- Risks for the run: scenario 4 needs at least 2 unclaimed VOLT lines; scenario 5 needs a negative return reversal among the proposals; a carried negative adjustment could trip batch_total_not_positive.
