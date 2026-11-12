# G1 — S8.1/S8.2 pure `rebate_matching` (branch `s8-matching`)

Written by the orchestrator: the G1 agent finished steps 1–5 (commits 648a563, 3601ed3, 0c012e0) but stopped at the
session limit before writing its report. Verification below was run by the orchestrator on 0c012e0.

## Built
- `models.py` frozen dataclasses (`ClaimLine`, `RemittanceLine`, `Allocation`, `Match`, `Note`, `MatchResult`,
  `Method` T1–T5/manual, `Status` suggested/confirmed); Decimal-only money (`_check_money`).
- `normalise.py` reference/SKU (aliases)/amount/date normalisation (§10.1).
- `config.py` `Weights`, `MatchConfig` (§10.2 weights, tolerances, T4/T5 limits) — one dataclass, `DEFAULT_CONFIG`.
- `engine.py` `reconcile(remittance_lines, claim_lines, *, existing, config, sku_aliases) -> MatchResult`:
  T1 exact reference, T2 invoice+line, T3 composite, T4 fuzzy (`scoring.py`, no external lib), T5 bounded subset-sum
  (`assignment.unique_subset_sum`); a tier accepts a pair only when unambiguous from both sides; only exact tiers
  auto-confirm; confirmed `existing` matches are kept and their lines excluded; limits produce notes
  (`assignment_limit_reached`, `combination_limit_reached`, `ambiguous_subset_sum`) instead of guesses.
  `manual_match`, `confirm_suggestion`, `validate_matches` (Σ allocations never exceed the remittance line's `amount_paid` → `InvariantViolation`). **Deviation from the brief:** the brief said "never exceeds a line's amount on either side"; the claim side is deliberately not capped, so an overpayment stays visible to §10.3 `overpaid` (§10.2 only requires Σ ≤ `amount_paid`).
- `assignment.maximise_assignment` deterministic optimal one-to-one assignment (integer weights, documented tie-break).
- `variance.py` `classify_claim_line`, `classify_unmatched_payment` (§10.3); `dispute.py` `assess_dispute(reason_code,
  DisputeFacts) -> DisputeAssessment` (§10.4).

## Verified (orchestrator, 2026-11-12, api container)
`pytest tests/matching` 75 passed (golden CM-7781 / RA-5521 / DN-310 from `expected_reconciliation.json`, Hypothesis
properties: allocation bounds, conservation, re-run keeps confirmed, input-order determinism; ambiguity and limit
cases); `mypy --strict src/rebate_matching` clean; import-linter 13 kept / 0 broken; ruff clean.

## Financial review (2026-11-12, independent, re-derived CM-7781 / RA-5521 / DN-310 by hand)
No financial discrepancy. Fixed: report claim above (blocker), CL-4 no-variance and GL-V2 confirm-then-no-variance
now asserted (minor 3). Logged follow-ups: re-run keep-confirmed property with changed inputs; richer Hypothesis
inputs (adjustments, zero payments, dates); T5 ambiguity across remittance lines (greedy by id, no note); two
remittance lines with one `claim_reference` match in neither T1 nor T2 (split payment) — document or allow;
RA-5521 L2 ground-truth wording; rapidfuzz/scipy named in TECH_STACK/§10.2 but reimplemented (doc update or ADR;
empty-vs-empty similarity 0 vs 100); `existing` ids missing from inputs raise KeyError; `max_assignment_dim=500`,
tolerance read as max(5 %, $5), integer Hungarian weights recorded as decisions.

## Open
- **Owner decision (§10.3 gap):** a confirmed match with `paid = 0` and no rejection code fits no row of the table;
  the package raises `UnclassifiableVariance` (fail closed). Recommendation: `short_paid` for the full amount with
  reason `short_paid_unexplained`.
- Interface consumer: S8 reconciliation module (remittance ingestion, persistence, workbench API) — next agent.
