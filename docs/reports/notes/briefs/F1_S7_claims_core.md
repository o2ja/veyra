# F1 — S7 claims core: S7.1–S7.3 (+ S7.6 tests for them) (branch `s7-claims`, test db `rebate_test_f1`)
Read COMMON.md, then ONLY: CLAUDE.md, IMPLEMENTATION_PLAN.md §S7, PRODUCT_REQUIREMENTS.md §1.6–1.7,
BUSINESS_LOGIC.md §7, §9.4, §11, §12, DATABASE_SCHEMA.md §3.8 + §7, API_SPECIFICATION.md rows for
`/missed-claims`, `/claim-export-templates`, `/claim-batches*`, SECURITY.md (exports / formula injection),
patterns: `modules/claims/*` (S5 runs/results), `modules/agreements` (four-eyes approve, If-Match),
`modules/files` + `core/jobs.py` (export file jobs), migrations of S5 (`20261114_0900_calc_runs`).
Core first, in this order; commit + push after each:
1. Migrations (one logical change each): `claim_export_templates`, `claim_batches`, `claim_lines`; RLS via helper;
   partial unique index = one active (released_at IS NULL) claim line per sales line / accrual entry; freeze and
   status-transition triggers (submitted lines immutable for the app role; draft→approved→submitted, draft|approved→void).
2. Missed-claims query + summary (§11) + `?format=csv|xlsx` export job.
3. Batch service + routes: create from result_ids or filter (409 `already_claimed` lists conflicts; concurrent
   creates cannot double-claim), claim reference generation, approve (four-eyes: approver ≠ creator), export job with
   template columns + formula-injection escaping, submit (Idempotency-Key, freezes lines, idempotent), void (releases lines).
4. Templates CRUD.
Tests (real Postgres): double-claim, concurrency (two sessions), freeze invariants via direct SQL as the app role,
export content + escaping, idempotent submit, totals tie out (batch total = Σ lines = Σ result claim_amounts, from
ground truth `expected_calculation.json` on a synthetic month), isolation suite + RLS-off negative control extended
to the 3 tables and all new routes, permission meta-test.
NOT yours: S7.4 adjustments (next agent), any frontend. Leave a clean `claims` service interface for adjustments
inclusion (`include_adjustment_ids`: accept the field, return 422 `not_supported_yet` is NOT allowed — instead leave
it out of the schema and note it for S7.4).
