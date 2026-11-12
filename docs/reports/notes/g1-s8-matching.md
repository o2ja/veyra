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
  `manual_match`, `confirm_suggestion`, `validate_matches` (allocation never exceeds either side → `InvariantViolation`).
- `assignment.maximise_assignment` deterministic optimal one-to-one assignment (integer weights, documented tie-break).
- `variance.py` `classify_claim_line`, `classify_unmatched_payment` (§10.3); `dispute.py` `assess_dispute(reason_code,
  DisputeFacts) -> DisputeAssessment` (§10.4).

## Verified (orchestrator, 2026-11-12, api container)
`pytest tests/matching` 75 passed (golden CM-7781 / RA-5521 / DN-310 from `expected_reconciliation.json`, Hypothesis
properties: allocation bounds, conservation, re-run keeps confirmed, input-order determinism; ambiguity and limit
cases); `mypy --strict src/rebate_matching` clean; import-linter 13 kept / 0 broken; ruff clean.

## Open
- Independent financial review (re-derive the golden values) required before merge.
- Interface consumer: S8 reconciliation module (remittance ingestion, persistence, workbench API) — next agent.
