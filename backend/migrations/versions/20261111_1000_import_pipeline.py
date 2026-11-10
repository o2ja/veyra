"""S3.3/S3.4 import pipeline and source transaction tables (DATABASE_SCHEMA.md §3.4, §3.5, §5, §7).

- mapping_templates, import_batches, import_staged_rows, import_row_errors (RLS).
- sales_documents/_lines, purchase_documents/_lines: append-only versions. Only ``is_current`` (true ->
  false) and ``superseded_by_id`` (NULL -> value) may ever change (trigger); no DELETE/TRUNCATE.
  Natural key unique ``WHERE is_current``; sign rules as CHECKs (BUSINESS_LOGIC.md §7).
- ``superseded_by_id`` FKs are DEFERRABLE INITIALLY DEFERRED so the loader can retire the old version
  and insert the new one in either order inside one transaction.

Revision ID: 20261111_1000_import_pipeline
Revises: 20261111_0900_masterdata
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261111_1000_import_pipeline"
down_revision = "20261111_0900_masterdata"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "mapping_templates",
    "import_batches",
    "import_staged_rows",
    "import_row_errors",
    "sales_documents",
    "sales_document_lines",
    "purchase_documents",
    "purchase_document_lines",
)
SOURCE_TABLES = ("sales_documents", "sales_document_lines", "purchase_documents", "purchase_document_lines")
IMPORT_TYPES = "('sales', 'purchases', 'customers', 'products', 'uom')"
TEMPLATE_TYPES = "('sales', 'purchases', 'customers', 'products', 'uom', 'agreement_items', 'remittance')"
UOM_CODE = "~ '^[A-Z0-9]{1,10}$'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE mapping_templates (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            name text NOT NULL,
            import_type text NOT NULL,
            header_signature char(64) NOT NULL,
            mapping jsonb NOT NULL,
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            version integer NOT NULL DEFAULT 1,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_by uuid,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_mapping_templates PRIMARY KEY (id),
            CONSTRAINT uq_mapping_templates_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_mapping_templates_tenant_id_name UNIQUE (tenant_id, name),
            CONSTRAINT fk_mapping_templates_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_mapping_templates_created_by FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT fk_mapping_templates_updated_by FOREIGN KEY (updated_by) REFERENCES users (id),
            CONSTRAINT ck_mapping_templates_name CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_mapping_templates_import_type CHECK (import_type IN {TEMPLATE_TYPES}),
            CONSTRAINT ck_mapping_templates_header_signature CHECK (header_signature ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_mapping_templates_mapping CHECK (jsonb_typeof(mapping) = 'object'),
            CONSTRAINT ck_mapping_templates_options CHECK (jsonb_typeof(options) = 'object'),
            CONSTRAINT ck_mapping_templates_version CHECK (version >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_mapping_templates_tenant_id_import_type_header_signature "
        "ON mapping_templates (tenant_id, import_type, header_signature)"
    )

    op.execute(
        f"""
        CREATE TABLE import_batches (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            import_type text NOT NULL,
            file_id uuid NOT NULL,
            mapping_template_id uuid,
            mapping_template_version integer,
            source_system text NOT NULL,
            status text NOT NULL,
            accept_partial boolean NOT NULL DEFAULT false,
            counts jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            error_summary text,
            checkpoint jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            detected_encoding text,
            delimiter text,
            sheet_name text,
            header_row integer,
            headers jsonb,
            header_signature char(64),
            mapping jsonb,
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            mapping_revision integer NOT NULL DEFAULT 0,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            confirmed_by uuid,
            confirmed_at timestamptz,
            completed_at timestamptz,
            CONSTRAINT pk_import_batches PRIMARY KEY (id),
            CONSTRAINT uq_import_batches_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_import_batches_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_import_batches_file_objects FOREIGN KEY (tenant_id, file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_import_batches_mapping_templates FOREIGN KEY (tenant_id, mapping_template_id)
                REFERENCES mapping_templates (tenant_id, id) ON DELETE SET NULL (mapping_template_id),
            CONSTRAINT fk_import_batches_created_by FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT fk_import_batches_confirmed_by FOREIGN KEY (confirmed_by) REFERENCES users (id),
            CONSTRAINT ck_import_batches_import_type CHECK (import_type IN {IMPORT_TYPES}),
            CONSTRAINT ck_import_batches_status CHECK (status IN ('uploaded', 'scanning', 'parsing',
                'awaiting_confirmation', 'loading', 'completed', 'completed_with_errors', 'failed', 'rejected')),
            CONSTRAINT ck_import_batches_source_system CHECK (source_system ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{{0,49}}$'),
            CONSTRAINT ck_import_batches_counts CHECK (jsonb_typeof(counts) = 'object'),
            CONSTRAINT ck_import_batches_checkpoint CHECK (jsonb_typeof(checkpoint) = 'object'),
            CONSTRAINT ck_import_batches_options CHECK (jsonb_typeof(options) = 'object'),
            CONSTRAINT ck_import_batches_error_summary CHECK (char_length(error_summary) <= 500),
            CONSTRAINT ck_import_batches_encoding
                CHECK (detected_encoding IN ('utf-8', 'utf-8-sig', 'cp1252', 'utf-16-le', 'utf-16-be', 'xlsx')),
            CONSTRAINT ck_import_batches_mapping_revision CHECK (mapping_revision >= 0),
            CONSTRAINT ck_import_batches_completed
                CHECK ((status IN ('completed', 'completed_with_errors')) = (completed_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_import_batches_tenant_id_created_at ON import_batches (tenant_id, created_at)"
    )
    op.execute("CREATE INDEX ix_import_batches_tenant_id_file_id ON import_batches (tenant_id, file_id)")
    op.execute(
        "ALTER TABLE unmapped_entities ADD CONSTRAINT fk_unmapped_entities_import_batches "
        "FOREIGN KEY (tenant_id, first_import_batch_id) REFERENCES import_batches (tenant_id, id)"
    )

    op.execute(
        """
        CREATE TABLE import_staged_rows (
            tenant_id uuid NOT NULL,
            import_batch_id uuid NOT NULL,
            row_number integer NOT NULL,
            raw jsonb NOT NULL,
            normalized jsonb,
            natural_key text,
            row_hash char(64),
            phase smallint NOT NULL DEFAULT 1,
            status text NOT NULL DEFAULT 'pending',
            CONSTRAINT pk_import_staged_rows PRIMARY KEY (import_batch_id, row_number),
            CONSTRAINT fk_import_staged_rows_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_import_staged_rows_import_batches FOREIGN KEY (tenant_id, import_batch_id)
                REFERENCES import_batches (tenant_id, id),
            CONSTRAINT ck_import_staged_rows_row_number CHECK (row_number >= 1),
            CONSTRAINT ck_import_staged_rows_status CHECK (status IN ('pending', 'valid', 'error', 'duplicate')),
            CONSTRAINT ck_import_staged_rows_phase CHECK (phase IN (1, 2)),
            CONSTRAINT ck_import_staged_rows_valid CHECK (status <> 'valid' OR (normalized IS NOT NULL
                AND natural_key IS NOT NULL AND row_hash IS NOT NULL))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE import_row_errors (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            import_batch_id uuid NOT NULL,
            row_number integer,
            column_name text,
            code text NOT NULL,
            message text NOT NULL,
            severity text NOT NULL DEFAULT 'error',
            raw jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_import_row_errors PRIMARY KEY (id),
            CONSTRAINT uq_import_row_errors_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_import_row_errors_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_import_row_errors_import_batches FOREIGN KEY (tenant_id, import_batch_id)
                REFERENCES import_batches (tenant_id, id),
            CONSTRAINT ck_import_row_errors_code CHECK (code ~ '^[a-z][a-z0-9_]{1,62}$'),
            CONSTRAINT ck_import_row_errors_message CHECK (char_length(message) BETWEEN 1 AND 500),
            CONSTRAINT ck_import_row_errors_column CHECK (char_length(column_name) <= 200),
            CONSTRAINT ck_import_row_errors_severity CHECK (severity IN ('error', 'warning'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_import_row_errors_tenant_id_import_batch_id "
        "ON import_row_errors (tenant_id, import_batch_id, row_number)"
    )

    _versioning_function()
    _successor_and_currency_functions()
    _sales_tables()
    _purchase_tables()

    for table in TENANT_TABLES:
        enable_tenant_rls(table)
    for table in SOURCE_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_versioned BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION fn_source_row_versioned()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_successor AFTER UPDATE OF superseded_by_id ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.superseded_by_id IS NOT NULL) "
            "EXECUTE FUNCTION fn_source_successor_check()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_currency BEFORE INSERT OR UPDATE OF currency_code ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION fn_source_currency_check()"
        )
        # Only the two versioning columns are ever updatable by the app role (column-level grant).
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM rebate_app")
        op.execute(f"GRANT UPDATE (is_current, superseded_by_id) ON {table} TO rebate_app")


def _versioning_function() -> None:
    op.execute(
        """
        CREATE FUNCTION fn_source_row_versioned() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'is_current' - 'superseded_by_id')
               IS DISTINCT FROM (to_jsonb(OLD) - 'is_current' - 'superseded_by_id') THEN
                RAISE EXCEPTION '%: source rows are versioned; only is_current and superseded_by_id may change',
                    TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
            END IF;
            IF NOT OLD.is_current AND NEW.is_current THEN
                RAISE EXCEPTION '%: a superseded version cannot become current again', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.superseded_by_id IS NOT NULL AND NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id THEN
                RAISE EXCEPTION '%: superseded_by_id is set once', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )


def _successor_and_currency_functions() -> None:
    # Deferred (commit-time) check of every retirement: the successor exists, is a different row with the same
    # natural key, is the next version and is current (DATABASE_SCHEMA.md §5).
    op.execute(
        """
        CREATE FUNCTION fn_source_successor_check() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE
            succ jsonb;
            cur jsonb := to_jsonb(NEW);
            k text;
            keys text[] := ARRAY['source_system', 'doc_type', 'document_number'];
        BEGIN
            IF TG_TABLE_NAME IN ('sales_document_lines', 'purchase_document_lines') THEN
                keys := keys || 'line_number'::text;
            END IF;
            IF NEW.superseded_by_id = NEW.id THEN
                RAISE EXCEPTION '%: a row cannot supersede itself', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
            END IF;
            EXECUTE format('SELECT to_jsonb(s) FROM public.%I s WHERE s.tenant_id = $1 AND s.id = $2', TG_TABLE_NAME)
                INTO succ USING NEW.tenant_id, NEW.superseded_by_id;
            IF succ IS NULL THEN
                RAISE EXCEPTION '%: successor % does not exist', TG_TABLE_NAME, NEW.superseded_by_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            FOREACH k IN ARRAY keys LOOP
                IF succ -> k IS DISTINCT FROM cur -> k THEN
                    RAISE EXCEPTION '%: successor has a different natural key (%)', TG_TABLE_NAME, k
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END LOOP;
            IF (succ ->> 'version')::int <> NEW.version + 1 OR NOT (succ ->> 'is_current')::boolean THEN
                RAISE EXCEPTION '%: successor must be the current version %', TG_TABLE_NAME, NEW.version + 1
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    # ADR-024: source amounts are in the tenant currency (validated equal to tenant_settings.currency_code).
    op.execute(
        """
        CREATE FUNCTION fn_source_currency_check() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE
            expected char(3);
        BEGIN
            -- A row for another tenant than the session's is refused by RLS WITH CHECK (runs after BEFORE
            -- triggers); that tenant's settings are invisible here, so leave the refusal to RLS.
            IF NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id', true), '')::uuid
               AND current_setting('app.tenant_id', true) <> '' THEN
                RETURN NEW;
            END IF;
            SELECT currency_code INTO expected FROM public.tenant_settings WHERE tenant_id = NEW.tenant_id;
            IF expected IS NULL OR NEW.currency_code IS DISTINCT FROM expected THEN
                RAISE EXCEPTION '%: currency % is not the workspace currency %', TG_TABLE_NAME, NEW.currency_code,
                    coalesce(expected, '(none)') USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )


def _sales_tables() -> None:
    op.execute(
        """
        CREATE TABLE sales_documents (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            import_batch_id uuid NOT NULL,
            source_system text NOT NULL,
            doc_type text NOT NULL,
            document_number text NOT NULL,
            document_date date NOT NULL,
            ship_date date,
            customer_id uuid NOT NULL,
            branch_id uuid,
            original_document_id uuid,
            currency_code char(3) NOT NULL,
            row_hash char(64) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            is_current boolean NOT NULL DEFAULT true,
            superseded_by_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_sales_documents PRIMARY KEY (id),
            CONSTRAINT uq_sales_documents_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_sales_documents_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_sales_documents_import_batches FOREIGN KEY (tenant_id, import_batch_id)
                REFERENCES import_batches (tenant_id, id),
            CONSTRAINT fk_sales_documents_customers FOREIGN KEY (tenant_id, customer_id)
                REFERENCES customers (tenant_id, id),
            CONSTRAINT fk_sales_documents_branches FOREIGN KEY (tenant_id, branch_id)
                REFERENCES branches (tenant_id, id),
            CONSTRAINT fk_sales_documents_original FOREIGN KEY (tenant_id, original_document_id)
                REFERENCES sales_documents (tenant_id, id),
            CONSTRAINT fk_sales_documents_superseded_by FOREIGN KEY (tenant_id, superseded_by_id)
                REFERENCES sales_documents (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_sales_documents_doc_type CHECK (doc_type IN ('invoice', 'credit_note')),
            CONSTRAINT ck_sales_documents_document_number CHECK (char_length(document_number) BETWEEN 1 AND 100),
            CONSTRAINT ck_sales_documents_currency_code CHECK (currency_code ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_sales_documents_row_hash CHECK (row_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_sales_documents_version CHECK (version >= 1),
            CONSTRAINT ck_sales_documents_current CHECK (is_current = (superseded_by_id IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sales_documents_natural_key ON sales_documents "
        "(tenant_id, source_system, doc_type, document_number) WHERE is_current"
    )
    op.execute(
        f"""
        CREATE TABLE sales_document_lines (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            document_id uuid NOT NULL,
            import_batch_id uuid NOT NULL,
            source_row_number integer NOT NULL,
            source_system text NOT NULL,
            doc_type text NOT NULL,
            document_number text NOT NULL,
            line_number integer NOT NULL,
            product_id uuid,
            sku_raw text NOT NULL,
            manufacturer_id uuid,
            customer_id uuid NOT NULL,
            description text,
            quantity numeric(18,6) NOT NULL,
            uom text NOT NULL,
            unit_price numeric(18,6) NOT NULL,
            unit_cost numeric(18,6),
            replacement_cost numeric(18,6),
            extended_amount numeric(18,4) NOT NULL,
            currency_code char(3) NOT NULL,
            invoice_date date NOT NULL,
            ship_date date,
            spa_reference text,
            project_reference text,
            original_line_id uuid,
            row_hash char(64) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            is_current boolean NOT NULL DEFAULT true,
            superseded_by_id uuid,
            source_changed boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_sales_document_lines PRIMARY KEY (id),
            CONSTRAINT uq_sales_document_lines_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_sales_document_lines_sales_documents FOREIGN KEY (tenant_id, document_id)
                REFERENCES sales_documents (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_import_batches FOREIGN KEY (tenant_id, import_batch_id)
                REFERENCES import_batches (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_products FOREIGN KEY (tenant_id, product_id)
                REFERENCES products (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_customers FOREIGN KEY (tenant_id, customer_id)
                REFERENCES customers (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_original FOREIGN KEY (tenant_id, original_line_id)
                REFERENCES sales_document_lines (tenant_id, id),
            CONSTRAINT fk_sales_document_lines_superseded_by FOREIGN KEY (tenant_id, superseded_by_id)
                REFERENCES sales_document_lines (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_sales_document_lines_doc_type CHECK (doc_type IN ('invoice', 'credit_note')),
            CONSTRAINT ck_sales_document_lines_quantity CHECK (quantity <> 0),
            CONSTRAINT ck_sales_document_lines_sign CHECK (
                (doc_type = 'invoice' AND quantity > 0 AND extended_amount >= 0)
                OR (doc_type = 'credit_note' AND quantity < 0 AND extended_amount <= 0)),
            CONSTRAINT ck_sales_document_lines_prices CHECK (
                unit_price >= 0 AND (unit_cost IS NULL OR unit_cost >= 0)
                AND (replacement_cost IS NULL OR replacement_cost >= 0)),
            CONSTRAINT ck_sales_document_lines_uom CHECK (uom {UOM_CODE}),
            CONSTRAINT ck_sales_document_lines_currency_code CHECK (currency_code ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_sales_document_lines_line_number CHECK (line_number >= 0),
            CONSTRAINT ck_sales_document_lines_source_row CHECK (source_row_number >= 1),
            CONSTRAINT ck_sales_document_lines_row_hash CHECK (row_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_sales_document_lines_version CHECK (version >= 1),
            CONSTRAINT ck_sales_document_lines_current CHECK (is_current = (superseded_by_id IS NULL)),
            CONSTRAINT ck_sales_document_lines_original CHECK (original_line_id IS NULL OR doc_type = 'credit_note')
        )
        """
    )
    for sql in (
        "CREATE UNIQUE INDEX uq_sales_document_lines_natural_key ON sales_document_lines "
        "(tenant_id, source_system, doc_type, document_number, line_number) WHERE is_current",
        "CREATE INDEX ix_sales_document_lines_manufacturer_date ON sales_document_lines "
        "(tenant_id, manufacturer_id, invoice_date) WHERE is_current",
        "CREATE INDEX ix_sales_document_lines_customer_date ON sales_document_lines "
        "(tenant_id, customer_id, invoice_date)",
        "CREATE INDEX ix_sales_document_lines_tenant_id_product_id ON sales_document_lines (tenant_id, product_id)",
        "CREATE INDEX ix_sales_document_lines_spa_reference ON sales_document_lines (tenant_id, spa_reference) "
        "WHERE spa_reference IS NOT NULL",
        "CREATE INDEX ix_sales_document_lines_tenant_id_original_line_id ON sales_document_lines "
        "(tenant_id, original_line_id)",
        "CREATE INDEX ix_sales_document_lines_tenant_id_import_batch_id ON sales_document_lines "
        "(tenant_id, import_batch_id)",
        "CREATE INDEX ix_sales_document_lines_tenant_id_document_id ON sales_document_lines "
        "(tenant_id, document_id)",
    ):
        op.execute(sql)


def _purchase_tables() -> None:
    op.execute(
        """
        CREATE TABLE purchase_documents (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            import_batch_id uuid NOT NULL,
            source_system text NOT NULL,
            doc_type text NOT NULL,
            document_number text NOT NULL,
            document_date date NOT NULL,
            manufacturer_id uuid NOT NULL,
            po_number text,
            currency_code char(3) NOT NULL,
            row_hash char(64) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            is_current boolean NOT NULL DEFAULT true,
            superseded_by_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_purchase_documents PRIMARY KEY (id),
            CONSTRAINT uq_purchase_documents_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_purchase_documents_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_purchase_documents_import_batches FOREIGN KEY (tenant_id, import_batch_id)
                REFERENCES import_batches (tenant_id, id),
            CONSTRAINT fk_purchase_documents_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_purchase_documents_superseded_by FOREIGN KEY (tenant_id, superseded_by_id)
                REFERENCES purchase_documents (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_purchase_documents_doc_type CHECK (doc_type IN ('receipt', 'vendor_credit')),
            CONSTRAINT ck_purchase_documents_document_number CHECK (char_length(document_number) BETWEEN 1 AND 100),
            CONSTRAINT ck_purchase_documents_currency_code CHECK (currency_code ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_purchase_documents_row_hash CHECK (row_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_purchase_documents_version CHECK (version >= 1),
            CONSTRAINT ck_purchase_documents_current CHECK (is_current = (superseded_by_id IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_purchase_documents_natural_key ON purchase_documents "
        "(tenant_id, source_system, doc_type, document_number) WHERE is_current"
    )
    op.execute(
        f"""
        CREATE TABLE purchase_document_lines (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            document_id uuid NOT NULL,
            import_batch_id uuid NOT NULL,
            source_row_number integer NOT NULL,
            source_system text NOT NULL,
            doc_type text NOT NULL,
            document_number text NOT NULL,
            line_number integer NOT NULL,
            product_id uuid,
            sku_raw text NOT NULL,
            manufacturer_id uuid NOT NULL,
            quantity numeric(18,6) NOT NULL,
            uom text NOT NULL,
            unit_cost numeric(18,6) NOT NULL,
            extended_amount numeric(18,4) NOT NULL,
            currency_code char(3) NOT NULL,
            document_date date NOT NULL,
            row_hash char(64) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            is_current boolean NOT NULL DEFAULT true,
            superseded_by_id uuid,
            source_changed boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_purchase_document_lines PRIMARY KEY (id),
            CONSTRAINT uq_purchase_document_lines_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_purchase_document_lines_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_purchase_document_lines_purchase_documents FOREIGN KEY (tenant_id, document_id)
                REFERENCES purchase_documents (tenant_id, id),
            CONSTRAINT fk_purchase_document_lines_import_batches FOREIGN KEY (tenant_id, import_batch_id)
                REFERENCES import_batches (tenant_id, id),
            CONSTRAINT fk_purchase_document_lines_products FOREIGN KEY (tenant_id, product_id)
                REFERENCES products (tenant_id, id),
            CONSTRAINT fk_purchase_document_lines_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_purchase_document_lines_superseded_by FOREIGN KEY (tenant_id, superseded_by_id)
                REFERENCES purchase_document_lines (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_purchase_document_lines_doc_type CHECK (doc_type IN ('receipt', 'vendor_credit')),
            CONSTRAINT ck_purchase_document_lines_quantity CHECK (quantity <> 0),
            CONSTRAINT ck_purchase_document_lines_sign CHECK (
                (doc_type = 'receipt' AND quantity > 0 AND extended_amount >= 0)
                OR (doc_type = 'vendor_credit' AND quantity < 0 AND extended_amount <= 0)),
            CONSTRAINT ck_purchase_document_lines_unit_cost CHECK (unit_cost >= 0),
            CONSTRAINT ck_purchase_document_lines_uom CHECK (uom {UOM_CODE}),
            CONSTRAINT ck_purchase_document_lines_currency_code CHECK (currency_code ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_purchase_document_lines_line_number CHECK (line_number >= 0),
            CONSTRAINT ck_purchase_document_lines_source_row CHECK (source_row_number >= 1),
            CONSTRAINT ck_purchase_document_lines_row_hash CHECK (row_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_purchase_document_lines_version CHECK (version >= 1),
            CONSTRAINT ck_purchase_document_lines_current CHECK (is_current = (superseded_by_id IS NULL))
        )
        """
    )
    for sql in (
        "CREATE UNIQUE INDEX uq_purchase_document_lines_natural_key ON purchase_document_lines "
        "(tenant_id, source_system, doc_type, document_number, line_number) WHERE is_current",
        "CREATE INDEX ix_purchase_document_lines_manufacturer_date ON purchase_document_lines "
        "(tenant_id, manufacturer_id, document_date) WHERE is_current",
        "CREATE INDEX ix_purchase_document_lines_tenant_id_import_batch_id ON purchase_document_lines "
        "(tenant_id, import_batch_id)",
        "CREATE INDEX ix_purchase_document_lines_tenant_id_document_id ON purchase_document_lines "
        "(tenant_id, document_id)",
    ):
        op.execute(sql)
