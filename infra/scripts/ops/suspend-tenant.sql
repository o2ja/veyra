-- Operator kill switch: suspend a workspace (every request gets 403, no data deleted). As rebate_owner:
--   psql -v tenant_slug=<slug> -v ticket_id=<ticket> -v operator_name=<name> -f suspend-tenant.sql
-- Add -v revoke_sessions=1 to also end every session of that workspace. Undo: unsuspend-tenant.sql
\set ON_ERROR_STOP on
\if :{?tenant_slug} \else \echo 'missing -v tenant_slug' \quit \endif
\if :{?ticket_id} \else \echo 'missing -v ticket_id' \quit \endif
\if :{?operator_name} \else \echo 'missing -v operator_name' \quit \endif
\if :{?revoke_sessions} \else \set revoke_sessions 0 \endif

BEGIN;
SELECT count(*) = 1 AS found FROM tenants WHERE slug = :'tenant_slug' \gset
\if :found
\else
  \echo 'no workspace with that slug; nothing changed'
  ROLLBACK;
  \quit
\endif
SELECT id AS tenant_id FROM tenants WHERE slug = :'tenant_slug' \gset
SELECT set_config('app.tenant_id', :'tenant_id', true) \g /dev/null

WITH old AS (
    SELECT id, status FROM tenants WHERE id = :'tenant_id' FOR UPDATE
), upd AS (
    UPDATE tenants t SET status = 'suspended' FROM old WHERE t.id = old.id RETURNING old.status AS old_status
)
INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, before, after, metadata)
SELECT :'tenant_id', now(), 'system', 'tenant.suspended_by_operator', 'tenant', :'tenant_id',
       jsonb_build_object('status', old_status), jsonb_build_object('status', 'suspended'),
       jsonb_build_object('ticket', :'ticket_id', 'operator', :'operator_name')
  FROM upd;

UPDATE sessions SET revoked_at = now()
 WHERE :revoke_sessions = 1 AND active_tenant_id = :'tenant_id' AND revoked_at IS NULL;
COMMIT;
\echo 'Suspended. Requests are refused now, with or without -v revoke_sessions=1.'
