-- Operator MFA reset (infra/runbooks/locked-out-admin.md §2). Run as rebate_owner:
--   psql -v user_id=<uuid> -v ticket_id=<ticket> -v operator_name=<name> -f mfa-reset.sql
\set ON_ERROR_STOP on
\if :{?user_id} \else \echo 'missing -v user_id' \quit \endif
\if :{?ticket_id} \else \echo 'missing -v ticket_id' \quit \endif
\if :{?operator_name} \else \echo 'missing -v operator_name' \quit \endif

BEGIN;
SELECT count(*) = 1 AS found FROM users WHERE id = :'user_id' \gset
\if :found
\else
  \echo 'expected exactly one user with that id; nothing changed'
  ROLLBACK;
  \quit
\endif

UPDATE users SET mfa_enabled = false, mfa_secret_enc = NULL, mfa_last_used_step = NULL, updated_at = now()
 WHERE id = :'user_id';
DELETE FROM mfa_recovery_codes WHERE user_id = :'user_id';
DELETE FROM trusted_devices WHERE user_id = :'user_id';
UPDATE sessions SET revoked_at = now() WHERE user_id = :'user_id' AND revoked_at IS NULL;

-- One audit row per active membership. RLS is forced for the owner too, so each insert sets the tenant.
SELECT format('SELECT set_config(''app.tenant_id'', %L, true)', tenant_id),
       format('INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, metadata) '
              'VALUES (%L, now(), ''system'', ''auth.mfa_reset_by_operator'', ''user'', %L, '
              'jsonb_build_object(''ticket'', %L, ''operator'', %L))',
              tenant_id, :'user_id', :'ticket_id', :'operator_name')
  FROM fn_user_memberships(:'user_id'::uuid) WHERE status = 'active' \gexec
COMMIT;
\echo 'MFA reset done: the user enrols again at next sign-in.'
