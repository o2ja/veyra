# Incident: suspected tenant data exposure

Status: **written, not exercised**. Use for any report or signal that one tenant may have seen, received or
altered another tenant's data, or that data left the system (cross-tenant response, leaked presigned URL,
stolen credential, misdirected export or email). Keep a timestamped log of every action from the first minute.
`C` = `docker compose --env-file .env.prod -f infra/docker/compose.prod.yml`.

## 1. Triage (first 30 minutes)

1. Open the incident log; name an incident lead. Do not delete anything.
2. Capture evidence before changing state: `$C logs --since 48h api worker > incident-<date>.log` (logs carry
   request ids, tenant ids and routes, not bodies), a `backup.sh` run, and the relevant `audit_log` rows.
3. Classify: (a) wrong-tenant read/write through the app, (b) credential/secret leak, (c) infrastructure access
   (host, database, object store, backup), (d) misdirected email/export.

## 2. Contain

- Suspected application bug (a): disable the affected route or module (stop `api`, or block the path in Caddy and
  reload: `$C exec caddy caddy reload --config /etc/caddy/Caddyfile`). Cross-tenant access must return 404; any 200
  with another tenant's data is a SECURITY.md section 2 failure.
- Suspected account takeover or credential leak (b): revoke sessions
  (`UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL` for all, or `WHERE user_id = ...`), rotate secrets
  (`secret-rotation.md`), force password resets for affected users.
- Leaked presigned URL (d): they expire within 5 minutes; if still inside the window, rotate the S3 keys.
- Infrastructure access (c): isolate the host (firewall to SSH from the operator only), snapshot the disk, rotate everything.

## 3. Scope: who saw what

Run as the superuser in a read-only transaction (it bypasses RLS on purpose; this is an investigation):

```sh
$C exec -T postgres psql -U postgres -d rebate
```
- Audit: `SELECT occurred_at, tenant_id, actor_user_id, action, ip FROM audit_log WHERE occurred_at BETWEEN '<from>' AND '<to>' ORDER BY occurred_at;`
  (check the table's exact column names with `\d audit_log`).
- Sessions: `SELECT id, user_id, active_tenant_id, ip, created_at FROM sessions WHERE ...` to find sessions whose active tenant
  differs from the user's memberships.
- Reproduce on the isolation suite: run the tenant-isolation tests against a restored copy
  (`backup-restore.md`), never against production data in place.
- Need to inspect one tenant's data as of a past moment: point-in-time restore into a scratch database and extract there.

## 4. Fix and recover

Fix the cause in code with a failing test first (extend `tests/isolation`), deploy via `deploy-rollback.md`.
Corrupted tenant data: restore the affected tables from a scratch restore, never by hand-editing production
(financial rows are immutable by design; corrections are adjustments).

## 5. Notify and close

Decide with the owner, within the hour, whether customer and regulator notification is required (legal terms are an
owner decision, not an engineering one; see the data-processing terms). Record: timeline, root cause, affected
tenants and data, actions, follow-ups. Add the missing test or alert. Update this runbook.
