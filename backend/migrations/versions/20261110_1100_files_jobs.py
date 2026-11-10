"""S2: file_objects (RLS, identity immutable), jobs queue (global), job-minted tokens

- file_objects: tenant table, RLS via helper, UNIQUE (tenant_id, id), duplicate detection by
  (tenant, kind, sha256) for import/remittance/agreement, trigger freezing the file's identity.
- jobs: the homegrown SKIP LOCKED queue (ADR-005 fallback). Global like Procrastinate's tables
  (SECURITY.md §2): payloads carry ids only; every read path filters tenant_id explicitly.
- Tokens are minted inside email jobs (S2 token decision): token_hash becomes nullable until then.
- password_reset_requests: the forgot-password job payload carries this row's id, never an email.

Revision ID: 20261110_1100_files_jobs
Revises: 20261110_1000_users_last_failed
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261110_1100_files_jobs"
down_revision = "20261110_1000_users_last_failed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE file_objects (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            kind text NOT NULL,
            original_name text NOT NULL,
            storage_key text NOT NULL,
            size_bytes bigint NOT NULL,
            sha256 char(64) NOT NULL,
            declared_mime text,
            detected_mime text NOT NULL,
            scan_status text NOT NULL DEFAULT 'pending',
            scan_detail text,
            uploaded_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            scanned_at timestamptz,
            CONSTRAINT pk_file_objects PRIMARY KEY (id),
            CONSTRAINT uq_file_objects_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_file_objects_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_file_objects_users FOREIGN KEY (uploaded_by) REFERENCES users (id),
            CONSTRAINT ck_file_objects_kind
                CHECK (kind IN ('import', 'agreement', 'remittance', 'export', 'attachment')),
            CONSTRAINT ck_file_objects_scan_status CHECK (scan_status IN ('pending', 'clean', 'infected', 'error')),
            CONSTRAINT ck_file_objects_size CHECK (size_bytes > 0),
            CONSTRAINT ck_file_objects_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_file_objects_name_length CHECK (char_length(original_name) BETWEEN 1 AND 255),
            CONSTRAINT ck_file_objects_key_prefix CHECK (
                storage_key = 'quarantine/' || tenant_id || '/' || id
                OR storage_key LIKE 'tenants/' || tenant_id || '/%/' || id
            ),
            CONSTRAINT ck_file_objects_scanned CHECK ((scan_status = 'pending') = (scanned_at IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_file_objects_tenant_id_kind_sha256 ON file_objects (tenant_id, kind, sha256) "
        "WHERE kind IN ('import', 'remittance', 'agreement') AND scan_status <> 'error'"
    )
    op.execute("CREATE INDEX ix_file_objects_tenant_id_created_at ON file_objects (tenant_id, created_at)")
    enable_tenant_rls("file_objects")
    # The bytes behind a file never change: only the scan outcome and (once) the storage key may.
    op.execute(
        """
        CREATE FUNCTION fn_file_objects_identity_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.tenant_id, NEW.id, NEW.kind, NEW.original_name, NEW.size_bytes, NEW.sha256,
                NEW.declared_mime, NEW.detected_mime, NEW.uploaded_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.id, OLD.kind, OLD.original_name, OLD.size_bytes, OLD.sha256,
                OLD.declared_mime, OLD.detected_mime, OLD.uploaded_by, OLD.created_at) THEN
                RAISE EXCEPTION 'file_objects identity columns are immutable' USING ERRCODE = 'restrict_violation';
            END IF;
            -- clean/infected are final; error may still become clean/infected when an admin retries the scan
            IF OLD.scan_status IN ('clean', 'infected') AND NEW.scan_status IS DISTINCT FROM OLD.scan_status THEN
                RAISE EXCEPTION 'scan outcome % is final', OLD.scan_status USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_file_objects_identity BEFORE UPDATE ON file_objects "
        "FOR EACH ROW EXECUTE FUNCTION fn_file_objects_identity_immutable()"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE ON file_objects FROM rebate_app"
    )  # deletion is a later audited process

    op.execute(
        """
        CREATE TABLE jobs (
            id uuid NOT NULL,
            tenant_id uuid,
            kind text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL DEFAULT 'queued',
            attempts integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 5,
            run_at timestamptz NOT NULL DEFAULT now(),
            locked_until timestamptz,
            last_error text,
            dedupe_key text,
            created_by uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            CONSTRAINT pk_jobs PRIMARY KEY (id),
            CONSTRAINT uq_jobs_dedupe_key UNIQUE (dedupe_key),
            CONSTRAINT fk_jobs_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_jobs_users FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_jobs_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
            CONSTRAINT ck_jobs_kind_format CHECK (kind ~ '^[a-z][a-z0-9_]{1,62}$'),
            CONSTRAINT ck_jobs_attempts CHECK (attempts >= 0 AND max_attempts BETWEEN 1 AND 20),
            CONSTRAINT ck_jobs_payload_object CHECK (jsonb_typeof(payload) = 'object'),
            CONSTRAINT ck_jobs_payload_size CHECK (pg_column_size(payload) <= 2048),
            CONSTRAINT ck_jobs_last_error_length CHECK (char_length(last_error) <= 500),
            CONSTRAINT ck_jobs_finished CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_jobs_queued_run_at ON jobs (run_at) WHERE status = 'queued'")
    op.execute("CREATE INDEX ix_jobs_running_locked_until ON jobs (locked_until) WHERE status = 'running'")
    op.execute("CREATE INDEX ix_jobs_tenant_id_status_created_at ON jobs (tenant_id, status, created_at)")

    op.execute(
        """
        CREATE TABLE password_reset_requests (
            id uuid NOT NULL,
            email citext NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_password_reset_requests PRIMARY KEY (id)
        )
        """
    )

    for table in ("invitations", "pending_registrations", "user_tokens"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN token_hash DROP NOT NULL")

    # Support/debug role: the queue spans all tenants and the reset table holds addresses.
    op.execute("REVOKE ALL ON jobs, password_reset_requests FROM rebate_readonly")
