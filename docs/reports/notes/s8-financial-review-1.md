S8 FINANCIAL REVIEW (first pass), s8-reconciliation @ edaa230, scope = git diff origin/s7-adjustments...HEAD
VERDICT: FAIL. One FINANCIAL DISCREPANCY (HARD STOP), 1 serious, 5 minor, 1 nit.

1. FINANCIAL DISCREPANCY (HARD STOP), not picking a side. Where: claims/recon_reads.py:32-45, reconciliation/matching.py:130-148, tests/reconciliation/support.py build_story.
   BL §10.3 says claimed = claim_amount "plus approved adjustments included in the same batch". The code nets every adjustment that is included in ANY submitted batch onto its original claim line. Ground truth (claim_batches_fixture) has ADJ-2026-0001 in the same batch as CL-...004, so GT, BL and code agree only for that ordering. The test puts the -50.00 in a second batch with INV-10020/1 312.50 (batch total 262.50) and expects no variance.
   Derivation, BL literal: CL-4 claimed 250.00, paid 200.00, so short_paid 50.00. Code: claimed 200.00, no case. Cross-batch double count: if VOLT pays the carrier batch 262.50 against INV-10020/1, the code gives INV-10020 short_paid 312.50-262.50 = 50.00 while the same -50.00 already reduced CL-4. GT is silent on this ordering, and it sits next to D-N1. The owner must define which batch's claimed an adjustment belongs to.
2. serious. Where: matching.py:584-592, 628-642. BL §10.3 makes finalisation require "all unmatched lines explicitly categorised". There is no categorisation step: finalise turns every unmatched line into unmatched_payment by itself. API_SPECIFICATION 2.7 checks only for open suggestions. BL is not updated and the deviation is not flagged.
3. minor. Where: variance.py:94-98 + matching.py:628. A remittance line with paid 0.00 and no match (a rejection that T1-T3 missed) becomes an unmatched_payment of 0.00. The claim line goes unpaid instead of rejected. A manual match cannot repair this: allocations must be > 0 (schemas.py:206-210) and can only point at claim lines, never adjustments.
4. minor. Where: variance.py:94-98. A line that is only partly allocated has no finding for the rest. Example: 4,430.00 with only 2,200.00 confirmed manually; the 2,230.00 is never reported. That matches BL's wording ("no accepted match") but is a money gap.
5. minor. Where: matching.py:605-611. Every finalisation of the same manufacturer reports unpaid again for every overdue unpaid line (CL-7 660.00 on each VOLT remittance). With the variance_cases partial unique index, an S9 hook that inserts naively will raise and roll back the finalisation. The S9 hook must upsert or skip.
6. minor. Where: matching.py:115-120, 189-202. A claim line with a confirmed allocation on another remittance is left out of the automatic tiers, so a top-up payment for a short-paid line becomes unmatched_payment. Only a manual match can fix it.
7. minor. Where: ingest.py:381-399. A CSV/XLSX file with no stated total is auto-accepted with total = sum of its lines, so the arithmetic check is empty. RA-5521 (568.00) and DN-310 (60.00) are accepted this way. The PDF path and a given total_amount are checked properly (intake.py:133-149, DB trigger 0900:157-159).
8. nit. Where: variance.py:83-85. Any external code counts as a "rejection code" for paid = 0, even a code mapped to a non-rejected_* reason.

RA-5521 120.00, re-derived: data row "", "inv-10099", 120.00 has no invoice-line column and no fixture claim, so no T1-T5 candidate. §10.3 "Remittance line with no accepted match" gives unmatched_payment = the line's amount_paid = 120.00. This is consistent with GT CM-7781 line 6 (75.00 = paid, not claimed 66.00). Line 1: 448.00 = SC-03 claim 448.00, so diff 0 and no case. Total 448+120 = 568.00. Agrees with the code.
CL-2026-000101 substitution: acceptable, not a hidden mismatch. The GT reference is a fixture label with no money attached. The system format is CL-YYYY-NNNNNN (batches.py:394-420). The test asserts method exact_reference/confirmed; T2/T3 cannot match (no line number, SKU or quantity), so a broken T1 would fail the test. Weakness: the cell equals the reference exactly, so reference normalisation (case/spaces) is not exercised.

CM-7781 re-derived (agrees with GT and the code): sum paid 2050+3120+0+200+2100+75 = 7545.00.
- L1: 2200-2050 = short_paid 150.00, E12 -> rejected_price_mismatch.
- L3: paid 0 with E07 = rejected 2080.00.
- L4: 250-50 = 200 = paid, no case (same-batch reading).
- L5: T5 1000+1100 = 2100, suggested; after confirmation no case.
- L6: unmatched_payment 75.00.
- CL-7: submitted 07-05 + lag 3 = 07-08 < today 07-10, so unpaid 660.00. The carrier batch (07-09 + 3 = 07-12) is not overdue.
DN-310: 60.00 = SC-12 60.00, invoice_line confirmed, no case.

Checked:
- Decimal parsing from text, scale 4, NUMERIC(18,4); no floats in the reconciliation module.
- Negative paid refused (row issue).
- Total mismatch +-0.01: code and DB trigger.
- Duplicate document number refused (create, job and correction path; IntegrityError mapped). Same file refused.
- T1/T2 auto-confirm; T3 confirms only on exact amount, tolerance max(5%, $5). T4/T5 suggested only. Ambiguity resolved from both sides (engine.py:60-66). The ambiguous-test expectations hold.
- Re-run keeps confirmed rows (code + test + DB immutability trigger). Rejected suggestions are not re-suggested.
- Allocations: sum <= amount_paid in the app and in the DB trigger (0910:162-170).
- Paid is derived (sum of confirmed allocations, adjustments included); claim tables are never written.
- Owner rule paid = 0 without a code gives short_paid_unexplained (variance.py:88-90).
- Sign: a deduction is a positive paid amount. Negative lines are refused per AI_PROCESSING §8.
Tests run: tests/reconciliation (+ unit -k reconcil/matching/variance/remittance) in rebate_test_rev3: 20 passed.
Not checked (outside the S8 diff): rebate_matching internals beyond the parts above; XLSX numeric-float cells (ingestion readers, S3).
