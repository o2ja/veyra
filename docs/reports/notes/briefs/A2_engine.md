# A2 — rebate_engine, the pure calculation package (S5.1–S5.2)

Agent id: `a2-engine`. Branch: `s5-engine`. Test DB: `rebate_test_a2` (you should not need a DB at all).

## Read
`CLAUDE.md`; `docs/BUSINESS_LOGIC.md` (ENTIRE, every section and worked example); `IMPLEMENTATION_PLAN.md` §S5
(S5.1, S5.2 only); `TESTING_STRATEGY.md` §3; `TEST_DATA_SPEC.md` §3; `testdata/generated/ground_truth/*`
(`expected_calculation.json`, `expected_rebates*.json`, `expected_volume_summary.json`, `master_manufacturers.json`),
`testdata/generated/agreements/manual_payloads/*.json`, the sales/purchases CSVs, and
`testdata/verify_business_logic_examples.py` (an independent check of BUSINESS_LOGIC examples; do not copy its
code into the engine — it is a second opinion, keep it independent).
Build the engine **only** from BUSINESS_LOGIC.md and ground truth. Where BUSINESS_LOGIC.md is ambiguous or
contradicts ground truth: STOP that item, write the exact contradiction in your report (section, values) — this
is a potential financial discrepancy that goes to the owner. Never "make the test pass" by bending a rule.

## Build `backend/src/rebate_engine/` (pure: import-linter `engine-purity` contract, `mypy --strict`, Decimal only)
- `money.py` (exists; extend: context, rounding modes), `models.py` (frozen dataclass inputs/outputs),
  `rules.py` (rules parsing from the agreement payload shape in `manual_payloads/*.json`), `uom.py`.
- `spa.py`: gates in the documented order, bases (price difference, % of sell/cost, flat, guaranteed margin), cap
  handling, returns netting/adjustments, claim window, exception codes.
- `rebate.py`: tier determination, retroactive/incremental, qualifying vs payout base, accrual deltas, claw-back.
- `rounding.py`: per-line and per-document (largest remainder).
- `trace.py`: trace builder + canonical hashing; `ENGINE_VERSION` constant.
- `adapters/` registry (mechanism only, empty registry).
- Design the inputs so the S5 orchestration (later agent) can feed them from DB rows: plain frozen dataclasses of
  Decimal/str/date/int; deterministic output order; no I/O, no clock, no randomness.

## Tests (S5.2) under `backend/tests/engine_golden/` and `backend/tests/unit/engine/`
- Every worked example in BUSINESS_LOGIC.md §14 (and every other numbered example) as a golden test, expected
  values typed from the doc.
- Ground-truth scenarios: build engine inputs directly from the testdata CSVs + manual payloads with a small test
  helper (stdlib csv; this is NOT the import pipeline, which comes later) and assert `expected_calculation.json`,
  `expected_rebates*.json`, `expected_volume_summary.json` exactly. If any scenario cannot be fed without the DB
  pipeline, say which and why.
- Property tests (hypothesis): an independent small reference implementation in the test tree for tiers and
  largest-remainder rounding; invariants (sum of rounded = rounded total, monotonic tiers, retro ≥ incremental
  where the doc says so, idempotence/byte-identical trace hash on rerun, order independence of inputs).
- A no-float check (test or lint) over `rebate_engine`.
- Run tests in the container (see COMMON.md) with `--no-cov` for speed while developing; at the end run
  `pytest tests/engine_golden tests/unit/engine` and the lint gates (ruff, mypy, import-linter).

## You own
`backend/src/rebate_engine/**`, `backend/tests/engine_golden/**`, `backend/tests/unit/engine/**`,
`docs/reports/notes/a2-engine.md`. Nothing else (no pyproject; if mypy strict config for the engine is missing,
report it).
