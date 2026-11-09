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
Run the reviewed script as `rebate_owner` from the operations host (one transaction, stops on any error, refuses unless exactly one user matches the **id**; never match on email):

```sh
psql -h <db-host> -U rebate_owner -d rebate   -v user_id=<uuid> -v ticket_id=<ticket> -v operator_name='<name>'   -f infra/scripts/ops/mfa-reset.sql
```

It turns MFA off, clears the secret and recovery codes, revokes every session of the user, and writes one `auth.mfa_reset_by_operator` audit row (actor `system`, ticket and operator in `metadata`) in each workspace where the user is an active member. Every workspace with `mfa_required = true` then forces enrolment at the next sign-in.

## 3. Sole admin disabled or gone
If the company names a successor who is **already a member**, promote them (also reactivates a disabled membership):

```sh
psql -h <db-host> -U rebate_owner -d rebate   -v tenant_id=<uuid> -v user_id=<uuid> -v ticket_id=<ticket> -v operator_name='<name>'   -f infra/scripts/ops/promote-admin.sql
```

It refuses if the user is not a member of that workspace, and writes `membership.role_changed_by_operator` with the old and new role/status. Never create accounts by SQL: if nobody suitable is a member, restore the original admin with §2 first.

**Dry run (2026-11-10, local database, SYNTHETIC data):** missing variable → stopped before any change; unknown user / non-member → "nothing changed", rolled back; real runs → MFA off, 0 recovery codes, 0 live sessions, audit rows present; the user was forced to enrol again at the next sign-in in Chrome. The first run exposed a missing `EXECUTE` grant for `rebate_owner` on `fn_user_memberships`; the script stopped and rolled back everything; fixed by migration `20261110_0900_owner_lookup_exec`.

## 4. After
- Tell the user by email (to the address on file) that MFA was reset and they must enrol again.
- Check the audit log in the workspace shows the operator entries.
- Close the ticket with the evidence summary. No secrets in the ticket.
