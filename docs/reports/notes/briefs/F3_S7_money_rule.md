# F3 — S7 money rule (owner decision D-N1/D-N3/D-N7, 2026-11-13)

Read `docs/reports/notes/briefs/COMMON.md` first; its rules apply. Branch: `s7-adjustments` (this worktree).
Test DB: `REBATE_TEST_DB=rebate_test_f3`. **Laptop is weak:** run ONLY the tests for code you change
(`pytest -x -q --no-cov <paths>`); never the full suite, never the negative control, never Playwright.
The orchestrator runs `make ci-local` at merge time.

## The owner's rule (verbatim; it is the spec)
> An adjustment belongs to the batch that carries it. Batch claimed totals, reconciliation (§10.3) and the
> dashboard count it in that batch; the original claim line is always reconciled at its frozen amount; a
> return already netted into a frozen claim creates NO return_reversal and NO agreement_change for that
> return (D-N1 correct value: 200.00).

## Background (D-N1)
Run 1 nets INV-10021 by CN-5002 (original not yet claimed) → 200.00, and the claim freezes at 200.00.
Run 2: the original is now claimed, so the engine computes on the gross quantity (250.00) and the return line
becomes `adjustment`; propose then emits +50.00 `agreement_change` AND −50.00 `return_reversal` on the same
line. Code: `rebate_engine/spa.py` (returns netting step), `modules/claims/adjustments.py` (agreement_change
and return_reversal loops), `modules/claims/frozen.py`. Review: PR #15 comment 6841512829 (`gh api`).

## Do
1. **Engine/adapter:** a claimed original is evaluated on the netting basis its frozen claim was made with.
   Returns netted into the frozen claim stay `netted` (amount 0) on later runs; only returns NOT netted into
   the frozen claim become `adjustment` (−prior unit amount × |qty|, §7 rounding). The adapter must know which
   return quantity (or which return lines) the frozen claim was netted by: use what is already persisted if it
   is sufficient and reliable (frozen calculation result / trace); if not, add the minimum persisted fact with a
   proper migration (tenant rules, RLS, immutability as for frozen claim data). Say which in your report.
   Keep `rebate_engine` pure (import-linter).
2. **Adjustments:** with (1) in place, no `agreement_change` and no `return_reversal` is proposed for a return
   already netted into the frozen claim. Other agreement_change cases are unchanged.
3. **Batch totals (S7 side of "belongs to the batch that carries it"):** a batch's claimed total = its claim
   lines at their frozen amounts + the approved adjustments that batch carries; an adjustment never changes
   the total of the batch holding the original line. Verify the current code does this; fix it if not.
4. **Docs:** update `docs/BUSINESS_LOGIC.md` §7 (the table rows for claimed originals and netted returns, plus
   one worked example for "return before claim, re-run after claim" = 200.00, no adjustment) and §10.3
   (`claimed` = the line's frozen `claim_amount`; an approved adjustment is reconciled in the batch that carries
   it, never added to its original line). Keep the wording short; cite "owner decision 2026-11-13".
   If you add a worked example, add it to `testdata/verify_business_logic_examples.py` the same way the others are.
5. **Golden tests (expected values derived by hand, derivation written as a comment next to each value):**
   - `return before claim`: INV-10021/CN-5002 → frozen claim 200.00; re-run after the claim proposes nothing
     for that line; a second return on the same original AFTER the claim still yields a return_reversal of
     −(frozen unit amount × its qty). Use the ground-truth inputs (`testdata/generated/ground_truth/*`) where
     they exist; hand-derive the rest from BUSINESS_LOGIC.
   - `adjustment carried in a later batch`: batch B1 holds the original line (frozen), batch B2 carries the
     approved adjustment → B1 total unchanged, B2 total includes the adjustment. Hand-derive the numbers.
   - The existing ground-truth case "return after claim" (`claim_batches_fixture.json`, 250.00 frozen, −50.00
     reversal) must still pass unchanged.
6. Commit in small steps, push `s7-adjustments`. Do not open or merge PRs. Do not edit PROJECT_STATUS.

## If the docs, ground truth and this rule disagree on any number: STOP and report it (HARD STOP). Do not pick.

## Report (≤ 60 lines)
SHA; what changed (files, migration id + down_revision if any); every test command run with counts; the hand
derivations of each golden value; anything not done or not tested; lint gates you ran (ruff, format, mypy,
lint-imports on the changed code).
