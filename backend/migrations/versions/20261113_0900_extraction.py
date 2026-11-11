"""S6.4 AI extraction storage: document_extractions, ai_invocations, rebate_ai_writer grants.

- document_extractions: tenant table (RLS ENABLE+FORCE). The app role may only SELECT, INSERT the *queued*
  request row (column-level INSERT grant: it cannot set results) and mark an unfinished row ``failed`` (column-level
  UPDATE of status/error columns, enforced by the trigger: dead-lettered jobs and stale rows must not block the
  file forever, even when the AI role is unreachable). Every result column is written by
  rebate_ai_writer through column-level UPDATE; a trigger freezes the identity columns and makes
  completed / needs_manual_entry / failed final (a re-run is a new row).
- ai_invocations: append-only usage ledger (cost, tokens, hashes; never prompts or responses). Only
  rebate_ai_writer inserts; the app role only reads (usage view, daily caps are checked by the gateway).
- rebate_ai_writer gets exactly: SELECT/UPDATE(result columns) document_extractions,
  SELECT/INSERT ai_invocations. agreement_drafts / agreement_draft_revisions are NOT granted although
  DATABASE_SCHEMA.md §2 allows them: the draft is written by the worker's app session because the shared
  AgreementValidator needs master-data reads that the AI role must not have (stricter than §2).
- agreement_drafts.extraction_id gets its foreign key (it had none until this migration, S4 note).

Revision ID: 20261113_0900_extraction
Revises: 20261112_0900_agreements
"""

from alembic import op

from migrations._immutable import make_append_only
from migrations._rls import enable_tenant_rls

revision = "20261113_0900_extraction"
down_revision = "20261112_0900_agreements"
branch_labels = None
depends_on = None

RESULT_COLUMNS = (
    "status, text_method, pass_results, result, evidence_check, validation, confidence, model, "
    "prompt_version, error_code, error_detail, completed_at"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_extractions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            file_id uuid NOT NULL,
            kind text NOT NULL,
            status text NOT NULL DEFAULT 'queued',
            text_method text,
            pass_results jsonb,
            result jsonb,
            evidence_check jsonb,
            validation jsonb,
            confidence numeric(4,3),
            model text,
            prompt_version text,
            error_code text,
            error_detail text,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT pk_document_extractions PRIMARY KEY (id),
            CONSTRAINT uq_document_extractions_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_document_extractions_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_document_extractions_file_objects FOREIGN KEY (tenant_id, file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_document_extractions_users FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_document_extractions_kind CHECK (kind IN ('agreement', 'remittance', 'mapping')),
            CONSTRAINT ck_document_extractions_status
                CHECK (status IN ('queued', 'processing', 'completed', 'needs_manual_entry', 'failed')),
            CONSTRAINT ck_document_extractions_text_method CHECK (text_method IN ('native', 'ocr', 'none')),
            CONSTRAINT ck_document_extractions_confidence CHECK (confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_document_extractions_completed
                CHECK (status <> 'completed' OR result IS NOT NULL),
            CONSTRAINT ck_document_extractions_failed CHECK (status NOT IN ('failed', 'needs_manual_entry')
                OR error_code IS NOT NULL),
            CONSTRAINT ck_document_extractions_final
                CHECK ((status IN ('completed', 'needs_manual_entry', 'failed')) = (completed_at IS NOT NULL)),
            CONSTRAINT ck_document_extractions_error_detail CHECK (char_length(error_detail) <= 500)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_document_extractions_active ON document_extractions (tenant_id, file_id, kind) "
        "WHERE status IN ('queued', 'processing')"
    )
    op.execute(
        "CREATE INDEX ix_document_extractions_tenant_id_file_id ON document_extractions (tenant_id, file_id, created_at)"
    )
    enable_tenant_rls("document_extractions")
    op.execute(
        """
        CREATE FUNCTION fn_document_extractions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.tenant_id, NEW.id, NEW.file_id, NEW.kind, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.id, OLD.file_id, OLD.kind, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'document_extractions identity columns are immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status IN ('completed', 'needs_manual_entry', 'failed') THEN
                RAISE EXCEPTION 'a finished extraction is final (re-run creates a new row)'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF current_user = 'rebate_app' AND (NEW.status <> 'failed' OR NEW.error_code IS NULL) THEN
                RAISE EXCEPTION 'the app role may only mark an unfinished extraction failed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            NEW.updated_at := now();  -- progress marker for the stale-row reaper
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_document_extractions_guard BEFORE UPDATE ON document_extractions "
        "FOR EACH ROW EXECUTE FUNCTION fn_document_extractions_guard()"
    )
    op.execute(
        "CREATE TRIGGER trg_document_extractions_no_delete BEFORE DELETE ON document_extractions "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_document_extractions_no_truncate BEFORE TRUNCATE ON document_extractions "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )

    op.execute(
        """
        CREATE TABLE ai_invocations (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            extraction_id uuid,
            purpose text NOT NULL,
            provider text NOT NULL,
            model text NOT NULL,
            prompt_version text NOT NULL,
            input_sha256 char(64),
            input_tokens integer,
            output_tokens integer,
            cost_usd numeric(12,6) NOT NULL DEFAULT 0,
            latency_ms integer,
            status text NOT NULL,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_ai_invocations PRIMARY KEY (id),
            CONSTRAINT uq_ai_invocations_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_ai_invocations_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_ai_invocations_document_extractions FOREIGN KEY (tenant_id, extraction_id)
                REFERENCES document_extractions (tenant_id, id),
            CONSTRAINT ck_ai_invocations_purpose
                CHECK (purpose IN ('agreement_pass_a', 'agreement_pass_b', 'agreement_repair', 'mapping', 'mapping_repair')),
            CONSTRAINT ck_ai_invocations_status CHECK (status IN ('ok', 'error', 'refused', 'invalid_output')),
            CONSTRAINT ck_ai_invocations_input_sha256 CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_ai_invocations_amounts
                CHECK (cost_usd >= 0 AND input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_ai_invocations_tenant_id_created_at ON ai_invocations (tenant_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_ai_invocations_tenant_id_extraction_id ON ai_invocations (tenant_id, extraction_id)"
    )
    enable_tenant_rls("ai_invocations")
    make_append_only("ai_invocations")

    op.execute(
        "ALTER TABLE agreement_drafts ADD CONSTRAINT fk_agreement_drafts_document_extractions "
        "FOREIGN KEY (tenant_id, extraction_id) REFERENCES document_extractions (tenant_id, id)"
    )

    # ---- privileges (reviewed): default privileges gave rebate_app full DML on new tables ----
    op.execute("REVOKE ALL ON document_extractions, ai_invocations FROM rebate_app")
    op.execute("GRANT SELECT ON document_extractions, ai_invocations TO rebate_app")
    op.execute(
        "GRANT INSERT (tenant_id, id, file_id, kind, status, created_by, created_at) "
        "ON document_extractions TO rebate_app"
    )
    op.execute("GRANT SELECT ON document_extractions, ai_invocations TO rebate_ai_writer")
    op.execute(f"GRANT UPDATE ({RESULT_COLUMNS}) ON document_extractions TO rebate_ai_writer")
    op.execute("GRANT INSERT ON ai_invocations TO rebate_ai_writer")
    op.execute(
        "GRANT UPDATE (status, error_code, error_detail, completed_at) ON document_extractions TO rebate_app"
    )
