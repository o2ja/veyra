-- Operator: restore a suspended workspace (status back to 'active'; sessions that were not revoked work again). As rebate_owner:
--   psql -v tenant_slug=<slug> -v ticket_id=<ticket> -v operator_name=<name> -f unsuspend-tenant.sql
-- Counterpart of suspend-tenant.sql; it never touches sessions, users or data.
\set ON_ERROR_STOP on
\if :{?tenant_slug} \else \echo 'missing -v tenant_slug' \quit \endif
\if :{?ticket_id} \else \echo 'missing -v ticket_id' \quit \endif
\if :{?operator_name} \else \echo 'missing -v operator_name' \quit \endif

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
    UPDATE tenants t SET status = 'active' FROM old WHERE t.id = old.id RETURNING old.status AS old_status
)
INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, before, after, metadata)
SELECT :'tenant_id', now(), 'system', 'tenant.unsuspended_by_operator', 'tenant', :'tenant_id',
       jsonb_build_object('status', old_status), jsonb_build_object('status', 'active'),
       jsonb_build_object('ticket', :'ticket_id', 'operator', :'operator_name')
  FROM upd;
COMMIT;
\echo 'Workspace is active again.'
