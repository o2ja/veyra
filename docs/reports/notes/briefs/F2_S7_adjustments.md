# F2 — S7.4 adjustments + frozen-claims wiring (branch `s7-adjustments`, from `origin/s7-claims`; test db `rebate_test_f2`)
Read COMMON.md, then ONLY: CLAUDE.md, IMPLEMENTATION_PLAN.md §S5.3 + §S7.4, BUSINESS_LOGIC.md §5.3, §5.5, §7, §9, §11,
§14 (SC-08, SC-09, GL-A1, R2), DATABASE_SCHEMA.md §3.8, API_SPECIFICATION.md rows `/claim-adjustments*`,
`/claim-batches` (`include_adjustment_ids`), `/calculation-runs/{id}/propose-adjustments`, the S7 core notes
`docs/reports/notes/f1-s7-claims.md`, `modules/claims/{frozen,batches,models,runs}.py`, S3 `source_changed` hook in
`modules/ingestion`, ground truth `claim_batches_fixture.json`, `expected_changed_rows.json`.
Orchestrator decisions (apply; document in DATABASE_SCHEMA §3.8 + your notes):
- `claim_lines.calculation_result_id` is NULL only for `source_kind = 'rebate_accrual'` (CHECK: sales_line ⇒ result +
  sales line set, accrual entry null; rebate_accrual ⇒ accrual entry set, result null). Migration chained on s7-claims' head.
- `claim_adjustments.claim_line_id` gets its composite FK now (update the S5 stand-in tests to use real claim lines).
Do, in order, commit + push after each:
1. `claims.frozen.source`: replace `NoFrozenClaims` with a `claim_lines` reader (submitted, not released) so runs see
   prior claims / consumption / frozen claims exactly as S5 specified; recalculation proposals compare against them.
2. Adjustments: return reversal, agreement change, rebate clawback (§7, §9.3) as proposals; `GET /claim-adjustments`,
   approve/reject (approve = approver, four-eyes); `include_adjustment_ids` on `POST /claim-batches` (only approved,
   same manufacturer, not already included; frozen with the batch); rebate-accrual claim lines (positive delta, §5.5).
3. `source_changed`: an import that changes a row with an active claim line flags it (S3 hook), visible on the line and
   as an adjustment proposal per the docs.
Tests (real Postgres; expected values from ground truth or hand derivation in docstrings): SC-08, SC-09, GL-A1, R2
clawback, batch with adjustments ties out (`claim_batches_fixture.json` batch_total incl. adjustment), double-inclusion
409, four-eyes, source_changed from `expected_changed_rows.json`, isolation suite + negative control for new
routes/columns. A financial reviewer re-derives every amount afterwards; do not merge.
