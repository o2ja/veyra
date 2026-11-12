"""S8.3a remittance_documents, remittance_lines, remittance_extraction_staging.

DATABASE_SCHEMA.md §3.9; BUSINESS_LOGIC.md §10.1; AI_PROCESSING.md §8. Tenant tables: composite FKs, RLS ENABLE +
FORCE via the shared helper.

- A document is inserted ``uploaded`` and moves ``uploaded -> pending_review -> accepted -> reconciled`` (or
  ``uploaded -> accepted`` for a clean CSV/XLSX, or ``-> void`` before acceptance). ``reconciled`` and ``void`` never
  change; an ``accepted`` document changes only to ``reconciled`` (finalisation columns). Identity columns are pinned.
- Acceptance is checked in the database: at least one line and |sum of line amounts - total_amount| <= 0.01
  (AI_PROCESSING §8), and acceptor + time recorded.
- ``remittance_lines`` are append-only evidence and can be inserted only while the document is not yet accepted
  (they are copied from the staging rows in the accepting transaction).
- ``remittance_extraction_staging`` holds the proposed lines (CSV mapping or the recorded-fixture PDF extraction) with
  their checks; editable only while the document is ``uploaded``/``pending_review``, then frozen as evidence.
- ``mapping_template_id`` (the template a CSV/XLSX was read with) replaces the schema doc's ``import_batch_id``: the
  remittance path reads files through the ingestion readers and mapping templates, not through an import batch.

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261117_0900_remittances
Revises: 20261116_0900_adjustments_wiring
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261117_0900_remittances"
down_revision = "20261116_0900_adjustments_wiring"
branch_labels = None
depends_on = None

MOVABLE = (
    "status",
    "document_number",
    "document_date",
    "payment_date",
    "total_amount",
    "currency_code",
    "extraction_id",
    "accepted_by",
    "accepted_at",
    "finalised_by",
    "finalised_at",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE remittance_documents (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            file_id uuid NOT NULL,
            doc_type text NOT NULL,
            channel text NOT NULL,
            document_number text,
            document_date date,
            payment_date date,
            total_amount numeric(18, 4) NOT NULL,
            currency_code text,
            status text NOT NULL DEFAULT 'uploaded',
            extraction_id uuid,
            mapping_template_id uuid,
            accepted_by uuid,
            accepted_at timestamptz,
            finalised_by uuid,
            finalised_at timestamptz,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_remittance_documents PRIMARY KEY (id),
            CONSTRAINT uq_remittance_documents_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_remittance_documents_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_remittance_documents_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_remittance_documents_file_objects FOREIGN KEY (tenant_id, file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_remittance_documents_document_extractions FOREIGN KEY (tenant_id, extraction_id)
                REFERENCES document_extractions (tenant_id, id),
            CONSTRAINT fk_remittance_documents_mapping_templates FOREIGN KEY (tenant_id, mapping_template_id)
                REFERENCES mapping_templates (tenant_id, id),
            CONSTRAINT fk_remittance_documents_users_accepted FOREIGN KEY (accepted_by) REFERENCES users (id),
            CONSTRAINT fk_remittance_documents_users_finalised FOREIGN KEY (finalised_by) REFERENCES users (id),
            CONSTRAINT fk_remittance_documents_users_created FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_remittance_documents_doc_type
                CHECK (doc_type IN ('credit_memo', 'remittance_advice', 'deduction_notice')),
            CONSTRAINT ck_remittance_documents_channel
                CHECK (channel IN ('credit_memo', 'check', 'ach', 'deduction')),
            CONSTRAINT ck_remittance_documents_status
                CHECK (status IN ('uploaded', 'pending_review', 'accepted', 'reconciled', 'void')),
            CONSTRAINT ck_remittance_documents_number CHECK (char_length(document_number) BETWEEN 1 AND 100),
            CONSTRAINT ck_remittance_documents_currency CHECK (currency_code ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_remittance_documents_total CHECK (total_amount >= 0),
            CONSTRAINT ck_remittance_documents_accepted CHECK (
                status = 'void'
                OR (status IN ('accepted', 'reconciled')) = (accepted_by IS NOT NULL AND accepted_at IS NOT NULL)),
            CONSTRAINT ck_remittance_documents_finalised CHECK (
                (status = 'reconciled') = (finalised_by IS NOT NULL AND finalised_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_remittance_documents_number ON remittance_documents "
        "(tenant_id, manufacturer_id, document_number) WHERE status <> 'void' AND document_number IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_remittance_documents_tenant_id_status_id ON remittance_documents (tenant_id, status, id)"
    )
    op.execute(
        "CREATE INDEX ix_remittance_documents_tenant_id_manufacturer_id ON remittance_documents "
        "(tenant_id, manufacturer_id, id)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_remittance_document_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'uploaded' THEN
                RAISE EXCEPTION 'remittance_documents: a new document is uploaded'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_remittance_document_update() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            lines_total numeric;
            lines_count integer;
        BEGIN
            IF OLD.status IN ('reconciled', 'void') THEN
                RAISE EXCEPTION 'remittance_documents: a % document never changes', OLD.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'document_number' - 'document_date' - 'payment_date' - 'total_amount' - 'currency_code' - 'extraction_id' - 'accepted_by' - 'accepted_at' - 'finalised_by' - 'finalised_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'document_number' - 'document_date' - 'payment_date' - 'total_amount' - 'currency_code' - 'extraction_id' - 'accepted_by' - 'accepted_at' - 'finalised_by' - 'finalised_at') THEN
                RAISE EXCEPTION 'remittance_documents: identity columns are fixed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status = 'accepted' AND (
                NEW.status <> 'reconciled'
                OR (to_jsonb(NEW) - 'status' - 'finalised_by' - 'finalised_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'status' - 'finalised_by' - 'finalised_at')) THEN
                RAISE EXCEPTION 'remittance_documents: an accepted document can only be finalised'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'uploaded' AND NEW.status IN ('pending_review', 'accepted', 'void'))
                OR (OLD.status = 'pending_review' AND NEW.status IN ('accepted', 'void'))
                OR (OLD.status = 'accepted' AND NEW.status = 'reconciled')) THEN
                RAISE EXCEPTION 'remittance_documents: % -> % is not a transition', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status = 'accepted' AND OLD.status <> 'accepted' THEN
                SELECT COALESCE(sum(amount_paid), 0), count(*) INTO lines_total, lines_count
                FROM remittance_lines WHERE tenant_id = NEW.tenant_id AND remittance_document_id = NEW.id;
                IF lines_count = 0 OR abs(lines_total - NEW.total_amount) > 0.01 THEN
                    RAISE EXCEPTION 'remittance_documents: line total % does not match the document total %',
                        lines_total, NEW.total_amount USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for name, verb in (("insert", "INSERT"), ("update", "UPDATE")):
        op.execute(
            f"CREATE TRIGGER trg_remittance_documents_{name} BEFORE {verb} ON remittance_documents "
            f"FOR EACH ROW EXECUTE FUNCTION fn_remittance_document_{name}()"
        )
    op.execute(
        "CREATE TRIGGER trg_remittance_documents_no_delete BEFORE DELETE ON remittance_documents "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_documents_no_truncate BEFORE TRUNCATE ON remittance_documents "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON remittance_documents FROM rebate_app")
    op.execute(f"GRANT UPDATE ({', '.join(MOVABLE)}) ON remittance_documents TO rebate_app")
    enable_tenant_rls("remittance_documents")

    op.execute(
        """
        CREATE TABLE remittance_lines (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            remittance_document_id uuid NOT NULL,
            line_number integer NOT NULL,
            source_row_number integer,
            claim_reference text,
            invoice_number text,
            invoice_line_number integer,
            sku_raw text,
            customer_ref text,
            quantity numeric(18, 6),
            amount_paid numeric(18, 4) NOT NULL,
            amount_claimed_stated numeric(18, 4),
            external_reason_code text,
            external_reason_text text,
            reason_code text,
            raw jsonb,
            CONSTRAINT pk_remittance_lines PRIMARY KEY (id),
            CONSTRAINT uq_remittance_lines_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_remittance_lines_line_number UNIQUE (tenant_id, remittance_document_id, line_number),
            CONSTRAINT fk_remittance_lines_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_remittance_lines_remittance_documents FOREIGN KEY (tenant_id, remittance_document_id)
                REFERENCES remittance_documents (tenant_id, id),
            CONSTRAINT fk_remittance_lines_reason_codes FOREIGN KEY (reason_code) REFERENCES reason_codes (code),
            CONSTRAINT ck_remittance_lines_line_number CHECK (line_number >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_remittance_lines_tenant_id_claim_reference ON remittance_lines "
        "(tenant_id, claim_reference)"
    )
    op.execute(
        "CREATE INDEX ix_remittance_lines_tenant_id_invoice ON remittance_lines "
        "(tenant_id, invoice_number, invoice_line_number)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_remittance_line_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            doc_status text;
        BEGIN
            SELECT status INTO doc_status FROM remittance_documents
            WHERE tenant_id = NEW.tenant_id AND id = NEW.remittance_document_id;
            IF doc_status NOT IN ('uploaded', 'pending_review') THEN
                RAISE EXCEPTION 'remittance_lines: lines can only be added before acceptance'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_lines_insert BEFORE INSERT ON remittance_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_remittance_line_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_lines_no_mutation BEFORE UPDATE OR DELETE ON remittance_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_lines_no_truncate BEFORE TRUNCATE ON remittance_lines "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON remittance_lines FROM rebate_app")
    enable_tenant_rls("remittance_lines")

    op.execute(
        """
        CREATE TABLE remittance_extraction_staging (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            remittance_document_id uuid NOT NULL,
            line_number integer NOT NULL,
            source_row_number integer,
            claim_reference text,
            invoice_number text,
            invoice_line_number integer,
            sku_raw text,
            customer_ref text,
            quantity numeric(18, 6),
            amount_paid numeric(18, 4),
            amount_claimed_stated numeric(18, 4),
            external_reason_code text,
            external_reason_text text,
            raw jsonb,
            evidence jsonb,
            issues jsonb NOT NULL DEFAULT '[]',
            corrected boolean NOT NULL DEFAULT false,
            CONSTRAINT pk_remittance_extraction_staging PRIMARY KEY (id),
            CONSTRAINT uq_remittance_extraction_staging_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_remittance_extraction_staging_line_number
                UNIQUE (tenant_id, remittance_document_id, line_number),
            CONSTRAINT fk_remittance_extraction_staging_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_remittance_extraction_staging_remittance_documents
                FOREIGN KEY (tenant_id, remittance_document_id) REFERENCES remittance_documents (tenant_id, id),
            CONSTRAINT ck_remittance_extraction_staging_line_number CHECK (line_number >= 1)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_remittance_staging_open() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            doc_status text;
            rec record;
        BEGIN
            rec := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            SELECT status INTO doc_status FROM remittance_documents
            WHERE tenant_id = rec.tenant_id AND id = rec.remittance_document_id;
            IF doc_status NOT IN ('uploaded', 'pending_review') THEN
                RAISE EXCEPTION 'remittance_extraction_staging: frozen once the document is accepted or void'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN rec;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_extraction_staging_open BEFORE INSERT OR UPDATE OR DELETE "
        "ON remittance_extraction_staging FOR EACH ROW EXECUTE FUNCTION fn_remittance_staging_open()"
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_extraction_staging_no_truncate BEFORE TRUNCATE "
        "ON remittance_extraction_staging FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE TRUNCATE ON remittance_extraction_staging FROM rebate_app")
    enable_tenant_rls("remittance_extraction_staging")
