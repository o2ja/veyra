# S8 review, fourth pass (fix diff 08393a4..b6d17b1) — 2026-11-13 evening

Verdict: NOT MERGEABLE. No financial discrepancy. All six third-pass rulings implemented (pure, service, trigger).
637 targeted tests pass; ruff, format, mypy, import-linter clean; single alembic head.

- SERIOUS A: the discriminating test for "a negative cross-batch allocation never frees room" is gone
  (test_reconciliation_csv.py:561-581): with −20 the refused +50.00 is refused under a signed sum too. Fix: second claim
  allocation = |ADJ allocation| (20.00: signed 200 fits, rule 220 > 200 refused) + a pure validate_matches case
  (paid 200; 200 claim, −50 cross-batch, +50 claim → refused). Stale docstrings :531-533, :494.
- MINOR B: per-item cap not race-safe and not in the DB (matching.py:549-561).
- MINOR C: +25.00 test commits an impossible frozen-S7 state under session_replication_role=replica (:617-683);
  leaves a confirmed 225.00 match behind.
- MINOR D: cap tests depend on file order (never_frees_room fails standalone: 201 ≠ 422).
- NIT E: recon_guards.py:47 FOR UPDATE vs FK FOR KEY SHARE → use FOR NO KEY UPDATE.
- NIT F: test_variance_dispute.py:326 calls a 200.00 line "CL-4 frozen" (GT CL-4 is 250.00).

Sent to builder G2 round 4 with orchestrator rulings.
