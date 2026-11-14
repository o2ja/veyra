# G2 — S8 money rule (owner decision D-N1/D-N3/D-N7, 2026-11-13)

Read `docs/reports/notes/briefs/COMMON.md` first; its rules apply. Branch: `s8-reconciliation` (worktree `.claude/worktrees/i1-s8`).
Test DB: `REBATE_TEST_DB=rebate_test_g2`. **Laptop is weak:** run ONLY the tests for code you change
(`pytest -x -q --no-cov <paths>`); never the full suite, never the negative control, never Playwright.
The orchestrator runs `make ci-local` at merge time.

## The owner's rule (verbatim; it is the spec)
> An adjustment belongs to the batch that carries it. Batch claimed totals, reconciliation (§10.3) and the
> dashboard count it in that batch; the original claim line is always reconciled at its frozen amount; a
> return already netted into a frozen claim creates NO return_reversal and NO agreement_change for that
> return (D-N1 correct value: 200.00).
D-N10 shortfalls (`agreement_change` carried by a later batch) are just another adjustment carried in a later batch: same rule.

## Step 0
`git merge origin/s7-adjustments` (brings F3: engine netting + new §7/§10.3 text). If F3 added a migration, re-chain so
there is one head. Use the new §10.3 wording; do not rewrite it.

## Where the code breaks the rule (review `docs/reports/notes/s8-financial-review-1.md` item 1)
- `backend/src/rebate/modules/claims/recon_reads.py:12-45`: `submitted_claim_lines` has no `batch_id`;
  `included_adjustments` returns every adjustment of ANY submitted batch. Add `c.batch_id` and `a.included_in_batch_id AS batch_id`.
- `backend/src/rebate/modules/reconciliation/matching.py:127-150` (`_claims`): attaches every adjustment to its original
  line (`adjustments=tuple(by_line...)`). Attach only adjustments whose carrying batch = the claim line's batch; keep the
  others as separate items in `State` (new field), never on the original line.
- `backend/src/rebate_matching/variance.py:29-40, 66-68`: `paid_for` sums claim AND adjustment parts onto the claim line, and
  `claimed = claim.net_amount`. Change: `claimed = claim.amount` (frozen); `paid` = confirmed allocations to that line with
  `adjustment_ref is None`. Add the adjustment-item side: `claimed = adjustment.amount`, `paid` = Σ allocations with that
  `adjustment_ref`; no case if `|diff| ≤ tol`.
- `backend/src/rebate_matching/models.py:42,58-60` (`net_amount` docstring "§10.3 claimed") and `engine.py:9-10,38-46`:
  `net_amount` stays ONLY as the expected payment for matching (T3 exact/tolerance), never as §10.3 claimed. `allocate()`
  keeps splitting same-batch adjustments at face value (ground truth L4 = 250.00 + −50.00).
- `matching.py:614-626`: `Finding.claimed = claim.net_amount` → `claim.amount`; `paid_for(...)` → claim part only.
- `rebate_matching` is outside the S8 diff but this brief allows editing it (keep it pure, `mypy --strict`, import-linter).

## Hand derivations (show these as comments next to the expected values)
Ground-truth ordering (`claim_batches_fixture.json`: ADJ-2026-0001 inside CB-VOLT-2026-06) — pure test
`tests/matching/test_variance_dispute.py::test_cm_7781_variances_match_ground_truth` must pass unchanged in its numbers:
- Batch total = 2200 + 3120 + 2080 + 250 + 1000 + 1100 + 660 = 10,410.00; − 50.00 = **10,360.00** (= GT `batch_total`).
- CM-7781 L4 pays 200.00 → allocations 250.00 (CL-4) + −50.00 (ADJ) per GT. CL-4: claimed 250.00, paid 250.00 → no case.
  ADJ item: claimed −50.00, paid −50.00 → no case. (GT `variance: null`.)

Real-flow ordering (`tests/reconciliation/support.py::build_story`: B1 = CB-VOLT, 7 lines, submitted 07-05;
B2 = INV-10020/1 312.50 + ADJ −50.00, submitted 07-09; finalise CM-7781 on 2026-07-10, VOLT lag 3 days):
- B1 claimed total = **10,410.00** (no adjustment carried). B2 claimed total = 312.50 − 50.00 = **262.50**.
- CM-7781 L4 (200.00, INV-10021/1, no reason code) matches CL-4 by `invoice_line`. ADJ is carried by B2, not CL-4's batch,
  so it is not auto-allocated: allocation **200.00 → CL-4** only. CL-4: claimed 250.00, paid 200.00, diff 50.00,
  paid > 0, no code → **short_paid 50.00, `short_paid_unexplained`** (NEW finding; review's "BL literal" value).
- B2: submitted 07-09 + 3 = 07-12 > 07-10 → no `unpaid` for INV-10020/1 or the ADJ.
- Findings on 07-10: short_paid 150.00 (CL-1, E12 → rejected_price_mismatch); rejected 2,080.00 (CL-3, E07);
  **short_paid 50.00 (CL-4)**; unmatched_payment 75.00 (L6); unpaid 660.00 (CL-7: 07-05 + 3 = 07-08 < 07-10).
- Cross-check (rule is self-consistent): claims 10,410.00 + 262.50 = 10,672.50; cash on matched lines
  2050 + 3120 + 0 + 200 + 2100 = 7,470.00; 10,672.50 − 7,470.00 = 3,202.50 = 150 + 2080 + 50 + 660 (cases) + 262.50 (B2, not yet due).
  If the −50.00 were still split onto L4, 50.00 would be counted paid in B1 and claimed in B2 (the review's double count).
- Update `support.py:209-212` (L4 now `[200.00]`, no adjustment allocation) and `check_findings` (add the CL-4 row),
  with a comment that the GT JSON describes the same-batch ordering, which the real flow cannot produce (an adjustment
  needs a frozen original, and adjustments are only included at batch creation, `claims/batches.py:394-399`).

## Orchestrator rulings (2026-11-13)
- **H1 confirmed:** an adjustment carried by another batch is never auto-allocated onto its original line's payment.
  CL-4 is reconciled at its frozen 250.00 → paid 200.00 → short_paid 50.00 (`short_paid_unexplained`). This is the
  value the owner's own D-N3 recommendation gave for the rule.
- **H2 is open (owner question D-N11); build no new rule for it.** Interim behaviour: an adjustment item carried by a
  batch is a separate reconcilable item that only gets an allocation when a person confirms a manual match with its
  `adjustment_ref` (no auto-allocation across items); an adjustment item gets **no finding** (no case, no unpaid);
  claim lines classify per §10.3 as written. No test asserts a number for a payment against B2; one test asserts that
  an adjustment item yields no finding. Note the interim in the §10.3 text as "pending owner decision D-N11".
- **Step 0 timing:** F3 (s7-adjustments) is still in its last review-fix round. Merge `origin/s7-adjustments` now, and
  again when the orchestrator tells you F3 is final.

## Other open findings from the review (fix all; same report)
1. **serious — explicit categorisation (§10.3 "all unmatched lines explicitly categorised"), `matching.py:584-592, 628-642`.**
   Add a persisted, audited per-line decision: `POST /remittance-lines/{id}/categorise {"category": "unmatched_payment"}`
   (only value for now; a line with a confirmed match is refused 409). Migration (new revision after
   `20261117_0910_recon_matches`, RLS as existing tables): `category`, `categorised_by`, `categorised_at` on
   `remittance_lines`. `finalise` refuses 409 `uncategorised_lines` while any line has neither a confirmed match nor a
   category; only categorised lines yield `unmatched_payment`. Update API_SPECIFICATION §2.7 and one §10.3 sentence.
2. **minor — paid 0.00 unmatched line (`variance.py:94-98`, `schemas.py:206-210`).** A 0.00 `unmatched_payment` would also
   make S9's `open_cases` raise (amount must be > 0) and roll back finalisation. Never emit a 0.00 finding; allow a manual
   match with `allocated_amount = 0` only when the line's `amount_paid = 0`, so the claim line classifies as `rejected`.
3. **minor — partial allocation remainder (`variance.py:94-98`).** Confirmed line with Σ allocations < amount_paid →
   `unmatched_payment` for the remainder (V2-style: 4,430.00 − 2,200.00 = 2,230.00). One sentence in §10.3.
4. **minor — repeated `unpaid` (`matching.py:605-611`).** S9 already skips it (`on_conflict_do_nothing`). Only document in the
   `on_finalise` docstring that findings repeat per finalisation. No code change.
5. **minor — top-up payments (`matching.py:115-120, 189-202`).** Keep T1/T2 (exact identifiers) open for a line with a
   confirmed allocation elsewhere whose `claimed − paid > tol`; T3–T5 still skip it.
6. **minor — no stated total (`ingest.py:381-399`).** CSV/XLSX without a total is not auto-accepted: 422 `total_required`
   until a person enters it. Tests pass totals RA-5521 = 448.00 + 120.00 = 568.00, DN-310 = 60.00 (review derivations).
7. **nit — `variance.py:83-85`.** paid 0 is `rejected` only if the code maps to a `rejected_*` reason or is unmapped
   (`unknown`); a code mapped to another reason → `short_paid` with that reason.

## Tests required (expected values hand-derived above, never from code output)
- `tests/matching/`: GT same-batch case unchanged; a pure different-batch case (CL 250.00 / paid 200.00 / ADJ in another
  batch → short_paid 50.00, ADJ no finding); ADJ item classification in the same-batch case; items 2, 3, 5, 7.
- `tests/reconciliation/`: updated story expectations; categorisation (refuse/accept/audit, 409 paths); zero-allocation
  manual match; no-total refusal; isolation route test for the new endpoint (`tests/isolation/test_routes.py -k categor`).
- Run: `pytest -x -q --no-cov tests/matching tests/reconciliation tests/isolation/test_routes.py tests/isolation/test_db_rls.py`.

## If the docs, ground truth and this rule disagree on any number: STOP and report it (HARD STOP). Do not pick.
Commit in small steps, push `s8-reconciliation`. Do not open or merge PRs. Do not edit PROJECT_STATUS / IMPLEMENTATION_PLAN.

## Report (≤ 60 lines)
SHA; files changed; migration id + down_revision; every test command with counts; the hand derivation of each golden
value; H1/H2 status as you found them; anything not done or not tested; lint gates run (ruff, format, mypy, lint-imports).
