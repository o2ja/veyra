-- Operator promotion of an existing member to admin (infra/runbooks/locked-out-admin.md §3). As rebate_owner:
--   psql -v tenant_id=<uuid> -v user_id=<uuid> -v ticket_id=<ticket> -v operator_name=<name> -f promote-admin.sql
\set ON_ERROR_STOP on
\if :{?tenant_id} \else \echo 'missing -v tenant_id' \quit \endif
\if :{?user_id} \else \echo 'missing -v user_id' \quit \endif
\if :{?ticket_id} \else \echo 'missing -v ticket_id' \quit \endif
\if :{?operator_name} \else \echo 'missing -v operator_name' \quit \endif

BEGIN;
SELECT set_config('app.tenant_id', :'tenant_id', true) \g /dev/null
SELECT count(*) = 1 AS found FROM memberships WHERE tenant_id = :'tenant_id' AND user_id = :'user_id' \gset
\if :found
\else
  \echo 'that user is not a member of that workspace; nothing changed (do not create accounts by SQL)'
  ROLLBACK;
  \quit
\endif

WITH old AS (
    SELECT id, role, status FROM memberships WHERE tenant_id = :'tenant_id' AND user_id = :'user_id' FOR UPDATE
), upd AS (
    UPDATE memberships m SET role = 'admin', status = 'active', updated_at = now()
      FROM old WHERE m.id = old.id
    RETURNING m.id, old.role AS old_role, old.status AS old_status
)
INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, before, after, metadata)
SELECT :'tenant_id', now(), 'system', 'membership.role_changed_by_operator', 'membership', id,
       jsonb_build_object('role', old_role, 'status', old_status), jsonb_build_object('role', 'admin', 'status', 'active'),
       jsonb_build_object('ticket', :'ticket_id', 'operator', :'operator_name')
  FROM upd;
COMMIT;
\echo 'Promotion done.'
