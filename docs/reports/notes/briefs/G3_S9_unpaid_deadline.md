# G3 — S9 unpaid-case deadline (owner decision D-N2, 2026-11-13)

Read `docs/reports/notes/briefs/COMMON.md` first. Laptop is weak: targeted tests only; never the full suite, negative control or Playwright. Expected values by hand. HARD STOP (report, do not pick) on any number disagreement. Commit, push `s9-recovery`, no PRs.

**Order:** start now on the current branch. Do NOT merge s8-reconciliation until the orchestrator says G2 is final.

- Rule: **unpaid-case deadline = claim submission date + manufacturer `dispute_window_days`**, whatever
  `dispute_window_basis` says (an unpaid line has no remittance). `deadline_basis = 'submission_date'` (allowed by
  `ck_` in `20261118_0900_recovery_cases.py:110`; no migration).
- Code: `backend/src/rebate/modules/recovery/service.py:188-214` (`_case_deadline`): for `f.case_type == "unpaid"` use
  the batch `submitted_at` (the existing `else` query). No setting → still `deadline_unknown`.
- Docs: BUSINESS_LOGIC §12 table, one row "Unpaid case: claim submission date + `dispute_window_days` (owner decision
  2026-11-13, D-N2)".
- Tests `tests/integration/test_recovery_cases.py`: unpaid CL-7 deadline **2026-09-03** (07-05 + 60: Jul 31 = +26,
  Aug 31 = +57, Sep 3 = +60), basis `submission_date`, `deadline_unknown` false (lines 172-173, 202-208); queue order
  (262-267: no unknown deadlines now; `deadline_before=2026-09-30` returns all four, CL-7 first); summary at-risk on
  07-10 (278-292) → count 0, 0.00. Update the docstrings with the arithmetic. If G2's CL-4 short_paid 50.00 reaches these
  tests (they build findings directly, line ~107), leave them as they are.
- Run: `pytest -x -q --no-cov tests/integration/test_recovery_cases.py tests/unit -k "deadline or recovery"`.
- Report ≤ 30 lines: SHA, files, tests with counts, derivations.
Test DB: `REBATE_TEST_DB=rebate_test_g3`. Pass -e REBATE_READONLY_PASSWORD -e REBATE_AI_WRITER_PASSWORD to the one-off container.
