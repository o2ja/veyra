# J1 - S9 recovery backend (branch `s9-recovery`, test db `rebate_test_j1`)

Stacked on `s8-reconciliation` as of merge 9cd5c24; origin/s8 has moved on (5d5184b and later). NOT re-merged here by order: the
orchestrator re-merges at the end. Expected conflicts: `frontend/src/api/openapi.json`, `schema.d.ts` (regenerate after the
merge with `make gen-client`), `docs/API_SPECIFICATION.md`, `tests/isolation/*` (both branches add rows/seeds), alembic head
(this branch's `20261118_0900_recovery_cases` has down_revision `20261117_0910_recon_matches`; re-check `alembic heads`).

## Built
- Migration `20261118_0900_recovery_cases`: `variance_cases`, `variance_case_events`, `case_attachments`, `notifications`
  (composite FKs, RLS ENABLE+FORCE, state-edge/identity/four-eyes triggers, append-only events and attachments).
- `modules/recovery`: pure `domain/` (deadlines, states, notifications; import-linter purity contract), `service.py`,
  `assessment.py` (dispute assessment from the frozen calculation), `jobs.py`, `router.py` (14 routes), `schemas.py`.
- Jobs: `recovery_sweep` (tenant-scoped, one per business day, self-chaining, deduped) and `recovery_digest` (one mail per
  user and day through the existing `core.mail.deliver`; `emailed_at` set only after the SMTP server accepted it).
  The mail abstraction existed, so nothing was added.

## Decisions (accepted before the stop, unchanged)
Only the most urgent reached reminder (14/7/3) is created per sweep; the deadline day itself still reminds (its key is the
3-day key, so nothing new); a case is `recovered` when recovered total >= case amount; no owner => all active admins and
approvers are notified; the dedupe key is (user, case:kind:offset).

## Deviations from the docs
- `deadline_unknown` is `deadline_date IS NULL`, not a case_type (BUSINESS_LOGIC 12 calls it a flag). `created_by_run_id` became
  `remittance_document_id`. Extra columns: `revision`, write-off proposal/approval columns, `external_reason_code`. Events also
  have `write_off_proposed` and `reclaim_proposed`. (Also in the migration docstring.)
- API spec 1 lists `Idempotency-Key` as required on `POST /cases/{id}/write-off`; the first cut did not implement it. Done now
  (reusing `ingestion.parse_key/replay/store`, endpoint key includes the case id); tests send a key on every case call.
- `GET /cases/{id}/attachments` exists in addition to the spec rows; API_SPECIFICATION 2.8 updated.

## Tests (all in the one worktree; DB `rebate_test_j1`)
- `tests/unit/test_recovery_domain.py` (72): D1-D4 goldens, leap year, DST, zone-vs-UTC midnight, every transition, four-eyes,
  dedupe, Hypothesis properties.
- `tests/integration/test_recovery_cases.py` (13): ground-truth remittance CM-7781 (150.00 short-pay, 2,080.00 rejection, 75.00
  unmatched, 660.00 unpaid; dispute deadline 2026-07-31 + 60 = 2026-09-29), queue order/paging/filters, summary, owner (412 / not a
  member), notes/attachments, every state-machine edge, reclaim -> adjustment, partial and full recovery, write-off four-eyes
  on/off, idempotent write-off (400 without key, replay, 409 on a different body), DB-level refusals.
- `tests/integration/test_recovery_jobs.py` (6, new): real Postgres + real Mailpit. Sweep on 2026-09-14 creates nothing; 09-15
  (14 days) creates 3 notifications per admin/approver and one digest each (subject and amounts 150.00 / 2080.00 / 75.00, not the
  unknown-deadline 660.00); a second sweep the same day creates nothing and mails nothing; an owned case notifies only its owner
  on 09-22; 09-26, deadline day 09-29 and 09-30 add nothing beyond the 3-day reminders (admin 7, approver 9 rows, 9 distinct keys);
  read marks only the caller's own (other user / other id 404).
- Isolation: `test_db_rls.py` S9 block (D2 select, D3 update, D5 insert, D9 delete/tenant move, table coverage) and
  `test_routes.py` (R1 for all `{case_id}` / `{notification_id}` routes with valid bodies, lists, filters, summary, owner membership).

## Final runs (commands: one-off api container, REBATE_TEST_DB=rebate_test_j1)
- `pytest -q --no-cov --ignore=tests/integration/test_end_to_end.py` (whole suite incl. tests/isolation, 4 deselected by config):
  2081 passed, 2 failed. Failure 1, `test_no_secret_column_ever_reaches_the_audit_log`: REAL bug of mine - the case audit snapshot
  had the key `reason_code`; renamed to `reason` in `service.view` (then the file set recovery+audit = 38 passed). Failure 2,
  `tests/unit/test_boundaries.py::test_every_business_module_has_a_protected_contract`: module `reconciliation` (I1/S8) has no
  protected contract in `importlinter.ini` (also missing on origin/s8); not mine, left for the S8 owner.
- Isolation alone before that: test_db_rls.py 271 passed, test_routes.py 108 passed. Negative control NOT run (orchestrator).
- `ruff check .`, `ruff format --check .`, `lint-imports`: clean. `mypy`: 1 error, `tests/integration/test_end_to_end.py`
  imports `FixtureClaims`, which `calc_support.py` lost in the main->s8 merge (it exists on origin/main); the same file also
  breaks pytest collection, so the suite above ignores it. Not mine; not fixed.
- Frontend: `npm run typecheck` ran after client regeneration (no output other than npm notices); `npm test` not run.

## Not done / not tested
- A digest whose SMTP send fails (retry, `emailed_at` stays null) is not tested: it needs a failing SMTP server and the shared
  Mailpit cannot be broken. The code path is `MailDeliveryError` from `core.mail`, retried by the job runner.
- Isolation negative control (`rebate_negctl`) not run by this agent (orchestrator at merge).
- Pre-existing, not mine: `mypy` reports `tests/integration/test_end_to_end.py:17 FixtureClaims` missing from
  `calc_support.py` (file arrived through the s8 merge; the same on origin/s8).
- Security and DB review, PR: after S8 merges.

## Interface for the UI brief
Routes in API_SPECIFICATION 2.8; case JSON carries `etag`, `revision`, `deadline_unknown`, `deadline_basis`, `dispute_assessment`;
all case writes need `If-Match`; write-off also `Idempotency-Key`. Notifications: `GET /notifications?unread=true`,
`POST /notifications/{id}/read`.

## Review 1 follow-up (docs/reports/notes/s9-review-1.md)
Merged origin/s8-reconciliation (edaa230 lineage); one alembic head (`20261118_0900_recovery_cases` on `20261117_0910_recon_matches`).
- S1: `reconciliation.on_finalise` hook registered in `recovery/__init__.py`; the copied dataclasses are gone (reconciliation's `Finding` /
  `FinalisedRemittance` are used). Test: finalising CM-7781 gives 150.00 / 2,080.00 / 75.00 / 660.00 via `GET /cases`
  (tests/reconciliation/test_reconciliation_csv.py). Repeated findings (the overdue `unpaid` 660.00 at every VOLT finalisation) are skipped by
  `ON CONFLICT DO NOTHING` on the live-case unique indexes, never an error: `test_a_second_finalisation_does_not_duplicate_a_live_case`.
  No S8 money logic was touched.
- S2: `claims.retire_recovery_reclaims` (rejects proposed/approved reclaims, 409 `reclaim_included` while in a batch) is called by the write-off in the same transaction.
- S3: audit allow-list now has `reason` and `write_off_reason`; asserted in the write-off audit test.
- S4: owner only while an active member (else active admins/approvers); digest and unsent selection only for active members.
- M1 DB CHECK `ck_variance_cases_written_off_amount`; M2 proposal voided on any recovery/status change (service, tested); M3 title states the real
  days ("in 2 days", "tomorrow", "today"); M4 `on_dead` of the sweep schedules the next day; M5 ETag on replay, `written_off_amount` in the summary,
  Decimal through the Money schema; M6 tests (submission_date basis, foreign file, blank text).
- DATABASE_SCHEMA.md 3.10 and API_SPECIFICATION 2.8 updated for the deviations.
- M7 (OWNER, not decided): with `remittance_date` basis an unpaid case has no deadline, so no reminders. Behaviour left as is.
