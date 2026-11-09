# Runbook: user locked out of MFA (including a workspace's sole admin)

Approved design: S1 decision D-6. There is **no in-app MFA reset**: a tenant admin cannot weaken a global account that may belong to other workspaces, and support has no standing access (`docs/SECURITY.md` §3). This runbook is the only path. Two operators should take part where possible (one executes, one verifies).

## When to use
- The user lost the authenticator **and** all recovery codes, or
- the only admin of a workspace is locked out (MFA lost, or the account is disabled), so nobody in the workspace can act.

A temporary lockout after wrong passwords (`423 account_locked`) is **not** a reason for this runbook: it expires by itself (15 min, then progressively longer). A forgotten password is handled by the normal reset flow.

## 1. Verify identity (outside the product)
1. The request must come through the support channel, not a phone call alone.
2. Confirm with **two independent signals**, for example: a reply from the account's email address **and** confirmation from a second known person at the customer company (another workspace member, or the contract signatory) through a contact on file.
3. For a sole-admin case, the customer company names who should be admin. Record the evidence (ticket id, names, time) in the ticket. Do not copy secrets or codes into the ticket.
4. If identity cannot be verified, stop. Do not proceed "to be helpful".

## 2. Reset MFA (operator, database owner connection)
Run as `rebate_owner` with `psql` from the operations host (`psql -v user_id=… -v ticket_id=… -v operator_name=…`), inside one transaction. Use the user's id, never a pattern match on email. `users`, `sessions` and `mfa_recovery_codes` are global tables (no RLS); tenant tables need `app.tenant_id` set even for the owner, because RLS is forced.

> **Not yet executed.** The S1 completion report records a dry run of this runbook against a local database.

```sql
BEGIN;
-- 1. Find the user (check exactly one row, matches the ticket)
SELECT id, email, status, mfa_enabled FROM users WHERE email = :'email';

-- 2. Clear MFA and void recovery codes
UPDATE users SET mfa_enabled = false, mfa_secret_enc = NULL, mfa_last_used_step = NULL
 WHERE id = :'user_id';
DELETE FROM mfa_recovery_codes WHERE user_id = :'user_id';

-- 3. Revoke every session of the user
UPDATE sessions SET revoked_at = now() WHERE user_id = :'user_id' AND revoked_at IS NULL;

-- 4. Audit: one row per tenant where the user is an active member.
--    FORCE ROW LEVEL SECURITY applies to rebate_owner too, so the tenant must be set.
--    fn_user_memberships is the sanctioned cross-tenant lookup (owned by the BYPASSRLS lookup role).
SELECT tenant_id FROM fn_user_memberships(:'user_id'::uuid) WHERE status = 'active';
--    Repeat the next two statements once per tenant_id listed (psql: -v tenant_id=…, or \set tenant_id …):
SELECT set_config('app.tenant_id', :'tenant_id', true);
INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, metadata)
VALUES (:'tenant_id'::uuid, now(), 'system', 'auth.mfa_reset_by_operator', 'user', :'user_id'::uuid,
        jsonb_build_object('ticket', :'ticket_id', 'operator', :'operator_name'));
COMMIT;
```

Every workspace with `mfa_required = true` forces the user to enrol again on next login (`403 mfa_enrollment_required` until done).

## 3. Sole admin disabled or gone
If the only admin's membership is disabled, or the person has left and the company names a successor who is already a member:

```sql
BEGIN;
SELECT set_config('app.tenant_id', :'tenant_id', true);
UPDATE memberships SET role = 'admin', status = 'active'
 WHERE tenant_id = :'tenant_id' AND user_id = :'successor_user_id';
INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, before, after, metadata)
VALUES (:'tenant_id', now(), 'system', 'membership.role_changed_by_operator', 'membership', :'membership_id',
        jsonb_build_object('role', :'old_role'), jsonb_build_object('role', 'admin'),
        jsonb_build_object('ticket', :'ticket_id', 'operator', :'operator_name'));
COMMIT;
```

If the successor is not yet a member, do not create accounts by SQL. Promote an existing member, or ask the customer to have the remaining admin-capable path (this runbook, section 2) restore the original admin first.

## 4. After
- Tell the user by email (to the address on file) that MFA was reset and they must enrol again.
- Check the audit log in the workspace shows the operator entries.
- Close the ticket with the evidence summary. No secrets in the ticket.
