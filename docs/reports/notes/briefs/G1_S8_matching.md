# G1 — S8.1 + S8.2: pure `rebate_matching` package (branch `s8-matching`, test db `rebate_test_g1` — not needed: pure)
Read COMMON.md, then ONLY: CLAUDE.md, BUSINESS_LOGIC.md §6 (rounding), §10 (all), TEST_DATA_SPEC.md §2.6,
`testdata/generated/ground_truth/expected_reconciliation.json`, `testdata/generated/remittance/*` (CSV/XLSX only; PDFs
are for a later agent), `backend/importlinter.ini`, the `rebate_engine` package layout (pattern: pure, frozen
dataclasses, Decimal, `mypy --strict`) and `tests/engine_golden/*` (test pattern).
Pure package in `backend/src/rebate_matching/` (no DB, network, clock, randomness, no new dependency; Hungarian/assignment
and subset-sum implemented here, bounded and deterministic). Build in this order, commit + push after each:
1. Models + normalisation (§10.1/§10.2 inputs: claim lines, remittance lines; reference/amount/date normalisation).
2. Tiers T1–T3 + manual-match representation; scoring/weights as a config dataclass (§10.2); only exact tiers may be
   `auto_confirm` (documented rule); evidence output per match (which fields matched, scores).
3. One-to-one assignment over candidates (optimal, deterministic tie-break documented), re-run preserves confirmed matches.
4. T4 fuzzy (as §10.2 defines; no external fuzzy lib), then T5 bounded subset-sum (limits from §10.2; report
   "limit reached" instead of guessing).
5. Variance classification (§10.3) and dispute assessment (§10.4) as pure functions over a match result.
Tests: golden tests reproducing the matching part of `expected_reconciliation.json` for CM-7781, RA-5521, DN-310
(expected values from that file or hand derivation, never from code output); Hypothesis property tests (allocation
never exceeds a line's amount on either side; Σ allocations conserve; re-run keeps confirmed; determinism under input
order); ambiguity/tie cases (two equal candidates → not auto-confirmed); subset-sum limit case. mypy --strict + import-linter clean.
Own only `backend/src/rebate_matching/**`, `backend/tests/matching/**` (new), importlinter contract lines if needed.
Interfaces you expose are used by the S8 reconciliation module (next agent): keep them small and documented in your report.
If §10 is ambiguous or contradicts the ground truth: STOP that part and report it (do not invent a rule).
