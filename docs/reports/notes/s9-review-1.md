VERDICT: NOT MERGEABLE. 0 blockers, 4 serious, 7 minor. No FINANCIAL DISCREPANCY. One owner question (M7).
Scope: S9 diff a9eab7b vs origin/s8-reconciliation. Ran in the api container (rebate_test_rev2): recovery unit, both recovery integration files and tests/security/test_audit_logs_config.py, 110 passed.

SERIOUS
S1 Cases are never created in the running app. Nothing outside recovery/ calls service.open_cases (recovery/__init__.py:5). origin/s8 has reconciliation.on_finalise(hook) (matching.py:100) with its own FinalisedRemittance (it adds document_number, payment_date and channel). This branch defines a second copy (service.py:52-71) and never registers it.
   Fix: at the re-merge, call reconciliation.on_finalise(...) with an adapter from the S8 types to open_cases, drop the copied dataclasses, and add one integration test: POST /remittances/{id}/finalize then GET /cases shows 150.00/2080.00/75.00/660.00.
S2 A write-off leaves the live reclaim adjustment in place. service.py:587-623 never touches claim_adjustments. A to_reclaim case can be written off while its recovery_reclaim adjustment is still proposed or approved. That adjustment can then be batched and sent to the manufacturer. The money it brings is ignored, because record_recoveries skips final cases (service.py:312). The books then show the written-off amount and the cash at the same time.
   Fix: in the same transaction, reject any proposed or approved reclaim for the case through the claims service, and refuse (409) while one is 'included'. Add a test for both.
S3 The commit a9eab7b audit "fix" drops the reason from the audit log. view() now emits "reason" (service.py:85), but AUDITED_FIELDS["variance_case"] still lists "reason_code" (audit/events.py:120-135). audit._allowed silently filters out "reason". The test went green because the field disappeared, not because it was renamed. write_off_reason is not audited either (US-16 requires the reason).
   Fix: replace "reason_code" with "reason" in the allow-list, add "write_off_reason", and assert both in the write-off audit test.
S4 Reminders go to deactivated users and nobody else. jobs.py:38-40 returns owner_user_id without checking the membership. jobs.py:126 mails users.email for any unsent row. An owner who has left therefore gets financial emails (amounts, manufacturer names), and the active approvers get no reminder.
   Fix: use the owner only when the membership is active (else fall back to admins and approvers), and send the digest only to active members.

MINOR
M1 written_off_amount = variance - recovered is enforced only in code (service.py:620). Add CHECK (status <> 'written_off' OR written_off_amount = variance_amount - recovered_amount) to migration :129.
M2 The four-eyes proposal records no amount and is never cleared (service.py:596-607). The approver can write off a different outstanding amount, much later. Store the proposed amount and require it to equal the outstanding amount at approval, or clear the proposal on any recovery or status change.
M3 The notification title says "deadline in {offset} days" (jobs.py:53,87). That is wrong for late-created cases and on the deadline day (it says 3). Use days_remaining(due, today).
M4 The sweep chain is self-enqueued only (jobs.py:110, service.py:281). One dead-lettered sweep stops all reminders until a new case opens. Add a daily bootstrap, or enqueue on worker start.
M5 The write-off replay returns no ETag (router.py:192). The summary returns raw str(Decimal) "1.0000" (service.py:408,424), not the Money schema. The written_off summary group sums variance_amount, not written_off_amount, so the PRD "written off" dashboard figure is wrong (service.py:403-411).
M6 Missing tests: dispute_window_basis = submission_date path (service.py:219), attaching another tenant's file_id to your own case (the isolation body is a zero UUID), whitespace-only reason or note (schemas.py:16,134 accept "   ").
M7 OWNER: with remittance_date basis, an unpaid case gets deadline NULL (service.py:217). So it gets no reminders at all, in the default config of every seeded manufacturer (testdata/generate.py:51-58). Ground truth gives only "2026-07-31 + 60 = 2026-09-29" per remittance and is silent on the unpaid 660.00. That is a gap, not a contradiction. Recommendation: owner decides (finalising document date, or submission_date).

MONEY (derived independently)
CM-7781: V1 2200.00 - 2050.00 = 150.00 short_paid; CL-3 paid 0 with E07 = 2080.00 rejected; line 6 = 75.00 unmatched; CL-7 = 660.00 unpaid. These match expected_reconciliation.json; S9 passes the amounts through and does not recompute them.
Deadline: 2026-07-31 + 60 = 2026-09-29. Reminders on 09-15, 09-22 and 09-26 match D2 arithmetic.
Recovery: apply_recovery gives recovered when total >= variance (states.py:84-85), backed by DB CHECK :125. Partial recovery stays submitted. Write-off = outstanding. Consistent with BL 12.1.

CHECKED, NO FINDING
Every route has a require(...) permission. Write-off needs case.write_off and an Idempotency-Key that is tenant- and case-scoped. Four-eyes is enforced in the service and by DB CHECK :131. Pydantic STRICT bodies. If-Match is parsed before the lookup (no existence leak). All 4 tables have RLS ENABLE+FORCE via the helper, composite FKs and explicit tenant filters. Notification read is filtered by user and returns 404 for others. Grants: DELETE/TRUNCATE revoked; notifications UPDATE is limited to read_at and emailed_at. Triggers cover state edges, identity, revision and append-only rows. Isolation covers all 4 tables and every {case_id}/{notification_id} route.

DEVIATIONS
deadline_unknown as NULL, remittance_document_id, the extra columns and events, and GET /cases/{id}/attachments are all acceptable. DATABASE_SCHEMA.md §3.10 is not updated for any of them (rule 14). Fix that in this change.
