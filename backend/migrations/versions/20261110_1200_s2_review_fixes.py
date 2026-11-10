"""S2 DB review fixes: final file rows fully frozen, bounded job attempts, no DELETE on jobs for the app

Revision ID: 20261110_1200_s2_review_fixes
Revises: 20261110_1100_files_jobs
"""

from alembic import op

revision = "20261110_1200_s2_review_fixes"
down_revision = "20261110_1100_files_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Identity columns never change. A clean/infected row never changes at all. The storage key may
    # change only together with the promotion to clean (quarantine -> clean key).
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
            IF OLD.scan_status IN ('clean', 'infected') AND NEW IS DISTINCT FROM OLD THEN
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
    op.execute("ALTER TABLE jobs ADD CONSTRAINT ck_jobs_attempts_max CHECK (attempts <= max_attempts)")
    op.execute("REVOKE DELETE, TRUNCATE ON jobs FROM rebate_app")  # dead letters are evidence
