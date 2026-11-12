# I1 — S8.3–S8.6 backend: remittance ingestion + reconciliation (branch `s8-reconciliation`, test db `rebate_test_i1`)
Base: `s7-adjustments` after F2 is done (S8 depends on S7 claim lines; merge `main` in too so `rebate_matching` incl.
PR #19 §10.3 is present). Read COMMON.md, then ONLY: CLAUDE.md, BUSINESS_LOGIC.md §10 (all) + §6 (rounding),
AI_PROCESSING.md §8, DATABASE_SCHEMA.md §3.9, API_SPECIFICATION.md (reconciliation/remittance sections + error envelope,
pagination, idempotency, If-Match), TEST_DATA_SPEC.md §2.6, `docs/reports/notes/g1-s8-matching.md` (package interface),
`testdata/generated/ground_truth/expected_reconciliation.json`, `testdata/generated/remittance/*`, and the `claims`
module (how claim lines are read; call its service, not its tables) and `ingestion` mapping templates (reuse them).
`rebate_matching` is done and reviewed: call `reconcile`, `manual_match`, `confirm_suggestion`, `validate_matches`,
`classify_*`, `assess_dispute`; do not re-implement matching or variance logic in the module.

Build in this order, commit + push after each:
1. S8.3 migrations (one logical change each; chain after the newest head on your base): `remittance_documents`,
   `remittance_lines`, `remittance_extraction_staging`, `reconciliation_matches` (+ allocations as the schema doc says).
   Tenant rules: `tenant_id`, composite unique/FKs, RLS ENABLE+FORCE via the helper, grants to `rebate_app`; confirmed /
   finalised rows immutable by trigger. Extend schema-lint + isolation suite for every new table.
2. S8.4 ingestion: CSV/XLSX through the existing mapping-template path (job, chunked, idempotent); duplicate
   remittance detection (§10); PDF via the extraction pipeline with RECORDED FIXTURES only (OD-2 deferred, no live
   LLM): staging rows, arithmetic checks (line sum vs document total → refuse acceptance on mismatch), human acceptance
   step before lines become matchable. AI never sets amounts that skip the check (CLAUDE.md rule 7).
3. S8.5 reconciliation job: load remittance + claim lines, call `reconcile` (keep confirmed on re-run), persist
   matches/allocations/evidence, variance classification, finalisation rules, channel handling (credit memo / check /
   ACH / deduction) per §10. Audit events in the same transaction.
4. S8.6 API (backend only; UI goes to Antigravity later): list/read remittances, lines, matches (cursor pagination);
   confirm / reject / manual match (If-Match), finalise (idempotency key); permissions per role on every route;
   cross-tenant → 404. Update OpenAPI + API_SPECIFICATION.md; regenerate the client.
Tests (S8.7 backend part): CM-7781 via CSV AND via the recorded PDF fixture reproduce exactly the expected matches,
allocations, cases and reasons in `expected_reconciliation.json`; RA-5521, DN-310; ambiguous/tie → not auto-confirmed;
arithmetic mismatch refused; duplicate remittance refused; re-run keeps confirmed; isolation tests for every route;
a large-remittance performance test (record the number, don't skip). Expected values from ground truth or hand
derivation, never from code output.
Own only: `backend/src/rebate/modules/reconciliation/**`, its migrations, `backend/tests/**/reconciliation*`, schema
lint/isolation registrations, the OpenAPI/API spec sections for reconciliation, generated client. Do not edit
`rebate_matching` (report needed changes instead).
If §10 / the schema doc / ground truth disagree on a money value or a rule: STOP that part and report it. Done = full
backend suite + isolation + negctl green, mypy/ruff/import-linter clean, notes in docs/reports/notes/i1-s8-reconciliation.md
(built, test counts, deviations, interface for the UI brief), pushed. Do not merge.
