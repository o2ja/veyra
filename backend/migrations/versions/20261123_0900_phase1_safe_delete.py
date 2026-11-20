"""Phase 1 UX: archive or delete unused files, discard imports.

- file_objects.archived_at: an archived file is hidden from ``GET /files`` by default (it stays readable and every
  record pointing at it keeps working). The immutability trigger lets a final (clean/infected) row be archived once;
  nothing else about it changes and archiving cannot be undone.
- fn_delete_unreferenced_file(id): the only way the app removes a file row. ``rebate_app`` keeps no DELETE grant
  (S2 design); the function (SECURITY DEFINER, owned by rebate_owner, which FORCE RLS still binds) deletes one
  non-archived row of the session's workspace. Any record that references the file (import, extraction, draft,
  version, remittance, case attachment, claim batch export) makes the composite foreign key refuse the delete
  (23503), so a file behind frozen data can never be removed; the service then archives it instead.
- import_batches.status gains the terminal ``discarded`` (a person dropped an import waiting for them).

Revision ID: 20261123_0900_phase1_safe_delete
Revises: 20261122_0900_dn11_signed_adj
"""

from alembic import op

revision = "20261123_0900_phase1_safe_delete"
down_revision = "20261122_0900_dn11_signed_adj"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE file_objects ADD COLUMN archived_at timestamptz")
    # Same rules as 20261110_1200, except that a final (clean/infected) row may be archived, once; archiving is final.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_file_objects_identity_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF (NEW.tenant_id, NEW.id, NEW.kind, NEW.original_name, NEW.size_bytes, NEW.sha256,
                NEW.declared_mime, NEW.detected_mime, NEW.uploaded_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.id, OLD.kind, OLD.original_name, OLD.size_bytes, OLD.sha256,
                OLD.declared_mime, OLD.detected_mime, OLD.uploaded_by, OLD.created_at) THEN
                RAISE EXCEPTION 'file_objects identity columns are immutable' USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.archived_at IS NOT NULL AND NEW.archived_at IS DISTINCT FROM OLD.archived_at THEN
                RAISE EXCEPTION 'archiving a file is final' USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.scan_status IN ('clean', 'infected')
               AND to_jsonb(NEW) - 'archived_at' IS DISTINCT FROM to_jsonb(OLD) - 'archived_at' THEN
                RAISE EXCEPTION 'scan outcome % is final', OLD.scan_status USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.storage_key IS DISTINCT FROM OLD.storage_key AND NEW.scan_status <> 'clean' THEN
                RAISE EXCEPTION 'storage key changes only on promotion to clean' USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_delete_unreferenced_file(p_file_id uuid) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
            t uuid := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
        BEGIN
            IF t IS NULL THEN
                RAISE EXCEPTION 'no workspace context' USING ERRCODE = 'insufficient_privilege';
            END IF;
            DELETE FROM public.file_objects WHERE tenant_id = t AND id = p_file_id AND archived_at IS NULL;
            RETURN FOUND;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION fn_delete_unreferenced_file(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION fn_delete_unreferenced_file(uuid) TO rebate_app")
    op.execute("ALTER TABLE import_batches DROP CONSTRAINT ck_import_batches_status")
    op.execute(
        "ALTER TABLE import_batches ADD CONSTRAINT ck_import_batches_status CHECK (status IN ('uploaded', "
        "'scanning', 'parsing', 'awaiting_confirmation', 'loading', 'completed', 'completed_with_errors', "
        "'failed', 'rejected', 'discarded'))"
    )
