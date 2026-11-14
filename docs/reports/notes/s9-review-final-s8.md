# S9 review on the final S8 + D-N2 (HEAD 0842bbe) — 2026-11-13 evening

Verdict: MERGEABLE WITH MINORS. No financial discrepancy. Re-derived by hand:
- D-N2 unpaid CL-7 660.00: submitted 2026-07-05 + 60 = 2026-09-03; reminders 08-20 (14 d), 08-27 (7 d), 08-31 (3 d).
- CL-4: frozen 250.00 (B1); ADJ-2026-0001 −50.00 carried by B2 → never nets CL-4 (D-N3), nothing auto-allocates (D-N11
  interim); line 4 pays 200.00 → short_paid 50.00, due 2026-07-31 + 60 = 2026-09-29.
- Other cases unchanged: 150.00 E12, 2,080.00 rejected, 75.00 unmatched_payment, 660.00 unpaid; cross-check
  10,672.50 − 7,470.00 = 3,202.50.
Merge: single head (recovery_cases after recon_guards); no S8 or S9 test lost (d9 split is S9's earlier reviewed rewrite).
Tests: 130 + 414 passed; ruff, format, mypy, lint-imports (16) clean.

Open:
1. minor: test_recovery_cases.py:435,458 confirm_payment inserts via admin_engine against a still-proposed reclaim
   (skips S8 guards). Add one test through the real flow: approve + include the reclaim adjustment in a batch, manual
   match via the API, finalise, case submitted/recovered.
2. minor: test_reconciliation_csv.py:390-396 fifth-case assertion lacks reason_code (expect short_paid_unexplained).
3. nit: test_reconciliation_csv.py:386 cite briefs/G2_S8_money_rule.md (H1/H2), not s8-financial-review-3.
4. nit: recovery/service.py:203-207 keep the `f.remittance_line_id is not None` guard (or a one-line comment).
