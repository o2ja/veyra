# Runbook: suspend / unsuspend a workspace (non-payment, abuse)

Suspending blocks every request of every user of the workspace at once (403 `tenant_suspended`, "Account suspended. Please contact support."), including existing sessions and trusted-device sign-ins, and blocks invitation acceptance. Nothing is deleted. Users who belong to other workspaces are unaffected.

Run as `rebate_owner` (see `locked-out-admin.md` for the connection). Each script writes one audit row (`tenant.suspended_by_operator` / `tenant.unsuspended_by_operator`) with the ticket and operator.

```
psql -v tenant_slug=<slug> -v ticket_id=<ticket> -v operator_name=<name> -f infra/scripts/ops/suspend-tenant.sql
psql -v tenant_slug=<slug> -v ticket_id=<ticket> -v operator_name=<name> -f infra/scripts/ops/unsuspend-tenant.sql
```

Add `-v revoke_sessions=1` to suspend to also end the workspace's sessions (users then sign in again; they see no workspace until it is unsuspended). Without it, sessions survive and work again on unsuspend. Find the slug: `SELECT slug, name, status FROM tenants;`.

Background jobs already queued for a suspended workspace keep running; stop them separately if that matters.
