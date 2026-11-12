# E1 — owner decision D2: widen BUSINESS_LOGIC §5.4 (branch `e1-d2-spa-lines`, test db `rebate_test_e1`)
Read COMMON.md rules/environment, then ONLY: CLAUDE.md, BUSINESS_LOGIC.md §4.0, §5.2–§5.4, §13, §14,
`src/rebate_engine/rebate.py`, `models.py` (VolumeLine), `modules/claims/loaders.py` (sales volume loader),
`tests/engine_golden/*`, the rebate parts of `testdata/generate.py` and `testdata/generated/ground_truth/expected_rebates*.json`.

**Decision D2 (owner, approved 2026-11-12):** a line is an SPA line if it has an SPA reference, OR its SPA result in
the same run is `eligible`, OR its SPA result is an exception with code `outside_claim_window` or `cap_exhausted`.
The trace records which signal(s) applied.

Do:
1. Engine + loader: carry the third signal (e.g. `spa_exception_signal`) on VolumeLine; `_spa_signals` and the
   trace (`spa_signals` entries) record all three. Keep the engine pure.
2. §5.4 text in BUSINESS_LOGIC.md (only that section and the §14 catalogue line you add): state the rule, cite
   "owner decision D2, 2026-11-12".
3. Golden tests: at least one line per new signal (outside_claim_window, cap_exhausted) under `include`,
   `exclude` and `qualify_only`, plus a line with another exception (e.g. price_mismatch) that is NOT an SPA line.
   Expected values derived by hand in the test docstring (show the arithmetic), never from code output.
4. Ground truth: if `testdata/generate.py` / `expected_rebates*.json` change, regenerate via the generator's own
   independent derivation and say exactly which values changed and why.
5. Integration: one run-level test proving the loader passes the signal (real Postgres).
Own only: rebate_engine/rebate.py, models.py (VolumeLine part), claims/loaders.py (volume part), engine_golden tests,
the new integration test, testdata generator rebate part + expected_rebates*.json, BUSINESS_LOGIC §5.4/§14.
Report: list every rebate amount that changed (agreement, period, before → after) with the hand derivation.
A financial reviewer will re-derive them independently; do not merge.
