# a2-engine notes (S5.1–S5.2, `rebate_engine`)

## A. Contradictions in BUSINESS_LOGIC.md (owner decision; potential financial discrepancy)

1. **§5.3 Example R1 / §14 GL-R1 "delta 10,000.00".** The example accrues 14,850.00 at 990,000, then says
   "purchases reach exactly 1,000,000 → delta = 25,000.00 − 15,000.00 = 10,000.00". Under the ledger rule of
   §5.2/§5.3 (`delta = earned_cum − prior_cumulative_accrued`) the prior accrual is 14,850.00, so the delta is
   **10,150.00**. A prior accrual of 15,000.00 (1,000,000 at tier 1) cannot exist because the tier bound is
   inclusive (§5.1). `verify_business_logic_examples.py` reproduces 10,000 by subtracting a hypothetical
   `r2(1,000,000 × 1.5%)`, not a ledger state. The engine implements the §5.2 formula; the golden test asserts
   the doc literal with prior 15,000.00 (formula holds). `expected_rebates.json` (14,850 → 10,400) agrees with
   the formula. Recommendation: reword R1 ("catch-up versus tier-1 rate on the same 1,000,000") or change the
   figure to 10,150.00.
2. **§5.4 SPA-line identification.** "An SPA line if it has a non-null `spa_reference` **or** … eligible" vs
   "if both signals disagree … the `spa_reference` wins". For a line with no reference that is SPA-eligible the
   two sentences give opposite answers (affects `qualify_only`/`exclude` payout base). The engine raises
   `EngineRuleError` in exactly that case when `spa_lines ≠ include`; no ground-truth case hits it.
   Recommendation: "OR" (count it as an SPA line, conservative against double dipping).

## B. Undefined rules — engine refuses (raises `EngineRuleError`) instead of guessing

3. `quantity.cap_scope = per_agreement`: the pool size is not defined (each item has its own `max_quantity`).
4. `tier_metric = quantity`: §5.2 defines `earned = V_p × rate` only for amounts; payout base for a unit metric
   is undefined.
5. `manufacturer_settings.claim_window_basis = period_end` (DATABASE_SCHEMA enum) has no definition in §4.0/§12.

## C. Interpretations taken (consistent with all ground truth; record in S5 report)

6. Cap consumption is keyed by **(agreement, product)** across versions. §4.2 says "per (agreement, item)" but
   also "under the same version/item"; version keys would reset caps at a split-supersede (§9.2) — found by the
   GL-A1 round-trip test. `SpaVersion.agreement_id`, `PriorClaim.agreement_id` carry it.
7. Customer scope: an `exclude` row on the customer or any ancestor wins over `include`; mode `all` honours
   `exclude` rows.
8. Claim UoM precedence: item `claim_uom` > rules `claim_uom` > product base UoM.
9. Exceptions store `claim_amount = 0` (GT SC-02 `"0.00"`), except `outside_claim_window` (amount kept).
10. Unclaimed original whose returns ≥ its quantity → `not_applicable` / `no_claim_due` (gate K's
    `cap_exhausted` would mislabel it).
11. Claimed (frozen) originals are recomputed on gross quantity (needed for §9.3); they consume
    `claimed qty − returned` of the cap at their position in the processing order. Lines ending
    `outside_claim_window` do not consume the cap. `prior_consumption` from the loader must exclude source lines
    present in the run.
12. Return adjustments: `−round(prior unit × |qty| in claim UoM)` with the claimed line's rounding rule; flag
    `cannot_be_net_claimed` when `allow_negative_claims` is false (DB default). GT SC-09r has no flags field.
13. `per_document` rounding groups lines that have an amount (eligible + outside window) by
    (document_number, agreement version).
14. Rebate qualifying volume below the first bound (net negative) → no tier, earned 0.
15. Trace: one `date_check` step per candidate; `result` also carries `flags`; `inputs_hash` = SHA-256 of the
    canonical trace (§13).
16. Payload items have no customer field, so items are keyed by product only (validator text mentions
    "(product, customer)").

## D. Additions outside the brief's file list (inside `rebate_engine/`)

- `deadlines.py` (§12 calendar-day deadlines; GL-D1/D2).
- `spa.propose_recalculation` (pure §9.3 comparison; S5.3/S7 wire it to frozen claim lines).

## E. Not covered here

- GL-V1/GL-V2 are reconciliation (`rebate_matching`, S8).
- DB end-to-end of the scenarios (S5.7), version "active as of" filtering, alias/parent-chain/return-link
  loading (S5.3 loaders).
