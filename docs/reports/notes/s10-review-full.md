# S10 full review (7054beb; S10 commits a54fe4b..ccb103c) — 2026-11-14

Verdict: MERGEABLE WITH MINORS. No financial discrepancy. Re-derived by hand: claimed 10,410.00 (CB-VOLT) + 262.50 (B2 =
312.50 − 50.00) = 10,672.50; collected 7,470.00 (6 allocations 2050, 3120, 0, 200, 1000, 1100); short_paid 2,940.00
(150 + 2080 + 50 + 660); cross-check 10,672.50 − 7,470 = 3,202.50 = 2,940 + 262.50; at_risk on 09-20 = 2,940.00,
expiring 2,280.00; D-N5 recovered_after_write_off 150.00; audit pack INV-10021 paid 200.00. GT 10,360.00 / 250+(−50)
splits are the same-batch scenario, settled by D-N3/D-N7/D-N11. Prior HARD STOP D-N7 and serious findings (UTC month,
two at-risk numbers) resolved. Tests: 16 + 569 passed; ruff, format, mypy, lint-imports (17) clean.

Open (sent to builder):
1. minor: test_reporting.py:53-55, :366 stale derivation comments (250/−50 on line 4) → 2050+3120+0+200+1000+1100.
2. minor: AUDIT_PACK_REQUESTED (audit/events.py:103) never written; request_audit_pack (reporting/jobs.py:73) must audit
   the request with who.meta in the request transaction.
3. minor: router.py:127 audit pack is a GET with side effects (CSRF-exempt). Orchestrator ruling: make it POST (route +
   API_SPECIFICATION + regenerated client).
4. minor: test_reporting_property.py:156-171 generates only open cases with recovered 0 → also generate status,
   recovered_amount ≤ variance, month/manufacturer filters.
5. minor: BUSINESS_LOGIC §12.2 does not say at_risk/expiring are bucketed by case creation month → document it.
Nits: "four routes/kinds" → five; no DST test; XLSX integers as text (exporting.py:228); orphan object if the job
crashes between storage.put and commit (jobs.py:134); export download gated only by data.read via /files/{id}.

Owner note (S8 scope, D-N11): under the interim rule, when B2 is paid 262.50 on INV-10020, ADJ-2026-0001 (−50.00) stays
unallocated, so INV-10020 would also show a 50.00 short pay next to CL-4's 50.00 — the same 50.00 counted twice. This is
what D-N11 must decide.
