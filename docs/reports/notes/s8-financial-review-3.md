# S8 financial + security + DB review, third pass (fresh), HEAD 08393a4 — 2026-11-13 evening

Verdict: NOT MERGEABLE. No financial discrepancy: every expected value re-derived from ground truth
(CB-VOLT 10,360.00; B1 10,410.00; B2 262.50; CL-4 short_paid 50.00; findings 150/2080/50/75/660; cross-check
10,672.50 − 7,470.00 = 3,202.50; V2 2,230.00; zero-match 660.00; RA-5521 568.00; DN-310 60.00).

1. SERIOUS: positive cross-batch adjustment allocations are uncounted cash (breaks §10.2). Line 225.00, CL-4 200.00,
   cross-batch ADJ +25.00: 200+25 → spurious unmatched_payment 25.00; 225+25 accepted, Σ 250.00 > 225.00.
   engine.py:90-93, variance.py:114-118, 20261118_0910_recon_guards.py:38-45, BUSINESS_LOGIC §10.3 row.
   Ruling: cross-batch allocation counts as max(amount, 0).
2. MINOR: no cumulative cap per adjustment item (matching.py:537-546).
3. MINOR: trigger lacks size/sign check on same-batch ADJ allocations. Ruling: must equal the adjustment amount.
4. MINOR: isolation test lacks foreign claim_adjustment_id on POST /matches.
5. MINOR: within_paid trigger does not serialise concurrent transactions (FOR UPDATE on the line).
6. NIT: test_reconciliation_csv.py:320 order dependency.

Tests: 635 passed (matching, reconciliation, security, isolation routes + RLS, db); ruff, format, mypy, import-linter clean.
Fixes sent to builder G2 round 3.
