# s7-adjustments resume (F2) — 2026-11-12 stop
State: f3824bf decision 1 + a057215 notes pushed; this WIP commit = fixes for the 2nd financial review (PR #15 comment 6834401619), UNVERIFIED (tests not run).
In WIP: new migration 20261117_0900_neg_claims_guard (down 20261116_0900_adjustments_wiring) = DB triggers for decision 1; frozen.py `_rows` back to _ACTIVE (B1); adjustments/batches changes; test_adjustment_flows.py; master_manufacturers.json line endings.
Left: B1 regression test (INV-10021 250.00 in draft, CN-5002 −50.00 approved, void → next batch 200.00, no reversal); adjustment trigger refuses inclusion of released/non-frozen line; S1 DB triggers + direct-DB tests; M1 re-check setting on approve/submit (batches.py:442,456); M2 docstrings; M3 proposal reason text; M4 line endings only VOLT change.
Then: ONE full suite run with REBATE_AI_WRITER_PASSWORD set, isolation, negctl, ruff, mypy, lint-imports → financial 2nd-pass on the fix diff → merge into s7-claims → PR #15.
