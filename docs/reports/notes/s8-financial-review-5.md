# S8 review, fifth pass (diff b6d17b1..0051a13) — 2026-11-13 evening

Verdict: MERGEABLE WITH MINORS. No financial discrepancy; every money value in new/changed tests re-derived by hand.
A–E fixed; revert check run for real (patched copy of rebate_matching: pure test "DID NOT RAISE", service test 500 ≠ 409).
Lock order checked: no deadlock cycle between manual() calls sharing one adjustment. Rolled-back test reaches the
deferred trigger (SET CONSTRAINTS ALL IMMEDIATE). Positive cross-batch via the service covered by the pure test only:
acceptable residual (service delegates to validate_matches).

Targeted: 638 passed, 0 failed (matching, reconciliation, security, isolation routes + RLS, db); ruff, format, mypy,
import-linter clean; single head 20261118_0910_recon_guards.

Open (sent to builder G2 round 5):
- F (half fixed): test_variance_dispute.py:326-329 still names the hypothetical line "CL-4" / c4 → CL-x / cx.
- Minor 1: decide() (matching.py:468-469) locks match rows before the document lock; manual() takes the document lock
  first → possible deadlock (500). Fix: resolve document without row locks, _locked, then _group.
- Minor 2: test_an_adjustment_item_is_matched_only_by_a_person_and_yields_no_finding fails standalone (:499).
- Nit 3: advisory-lock key → hashtextextended('recon-adj:'||id, 0) in service and trigger (codebase convention).
- Nit 4: concurrent over-cap loser gets 500 from the trigger at commit — accepted as a backstop.
