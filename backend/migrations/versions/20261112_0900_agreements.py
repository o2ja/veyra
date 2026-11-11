"""S4.1 agreements: agreements, agreement_drafts (+revisions), agreement_versions (+items, customers, tiers).

DATABASE_SCHEMA.md §3.6, §7; BUSINESS_LOGIC.md §9; ADR-010/011. Every table: tenant_id, composite FKs, RLS via
the shared helper. Invariants in the database:

- agreement_versions: only approved versions exist. ``valid_range`` is generated; an exclusion constraint
  forbids overlapping ``active``/``expired`` versions of one agreement (expired versions still govern their
  period). Terms are immutable: the trigger allows only ``status`` (active -> superseded/expired/cancelled,
  expired -> superseded/cancelled), ``superseded_at`` and ``superseded_by_version_id`` (set once) to change,
  and the app role holds UPDATE on exactly those columns; no DELETE/TRUNCATE. ``created_txid`` (forced by
  trigger) records the inserting transaction.
- items/customers/tiers: append-only, and rows can only be added in the transaction that created the parent
  version, so the terms of an approved version can never be extended later either (even with direct SQL).
- agreement_draft_revisions: append-only. drafts/agreements: no DELETE.

Revision ID: 20261112_0900_agreements
Revises: 20261111_1000_import_pipeline
"""

from alembic import op

from migrations._immutable import make_append_only
from migrations._rls import enable_tenant_rls

revision = "20261112_0900_agreements"
down_revision = "20261111_1000_import_pipeline"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "agreements",
    "agreement_drafts",
    "agreement_draft_revisions",
    "agreement_versions",
    "agreement_version_items",
    "agreement_version_customers",
    "agreement_version_tiers",
)
CHILD_TABLES = ("agreement_version_items", "agreement_version_customers", "agreement_version_tiers")
SOURCES = "('manual', 'spreadsheet_template', 'ai_extraction')"
UOM_CODE = "~ '^[A-Z0-9]{1,10}$'"
RATE = "BETWEEN 0 AND 1"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agreements (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            agreement_type text NOT NULL,
            external_number text NOT NULL,
            name text NOT NULL,
            cancelled_at timestamptz,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_agreements PRIMARY KEY (id),
            CONSTRAINT uq_agreements_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_agreements_tenant_id_manufacturer_id_external_number
                UNIQUE (tenant_id, manufacturer_id, external_number),
            CONSTRAINT fk_agreements_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreements_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_agreements_users FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_agreements_agreement_type CHECK (agreement_type IN ('spa', 'rebate')),
            CONSTRAINT ck_agreements_external_number CHECK (char_length(external_number) BETWEEN 1 AND 100),
            CONSTRAINT ck_agreements_name CHECK (char_length(name) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE agreement_drafts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            agreement_id uuid,
            amends_version_id uuid,
            source text NOT NULL,
            status text NOT NULL DEFAULT 'draft',
            payload jsonb NOT NULL,
            payload_schema_version integer NOT NULL,
            revision integer NOT NULL DEFAULT 1,
            validation jsonb,
            extraction_id uuid,
            source_file_id uuid,
            created_by uuid NOT NULL,
            submitted_by uuid,
            submitted_at timestamptz,
            reviewed_by uuid,
            reviewed_at timestamptz,
            rejection_reason text,
            review_comment text,
            resulting_version_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_agreement_drafts PRIMARY KEY (id),
            CONSTRAINT uq_agreement_drafts_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_agreement_drafts_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreement_drafts_agreements FOREIGN KEY (tenant_id, agreement_id)
                REFERENCES agreements (tenant_id, id),
            CONSTRAINT fk_agreement_drafts_file_objects FOREIGN KEY (tenant_id, source_file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_agreement_drafts_created_by FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT fk_agreement_drafts_submitted_by FOREIGN KEY (submitted_by) REFERENCES users (id),
            CONSTRAINT fk_agreement_drafts_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES users (id),
            CONSTRAINT ck_agreement_drafts_source CHECK (source IN {SOURCES}),
            CONSTRAINT ck_agreement_drafts_status CHECK (status IN
                ('draft', 'pending_approval', 'approved', 'rejected', 'needs_manual_entry', 'cancelled')),
            CONSTRAINT ck_agreement_drafts_payload CHECK (jsonb_typeof(payload) = 'object'),
            CONSTRAINT ck_agreement_drafts_payload_schema_version CHECK (payload_schema_version >= 1),
            CONSTRAINT ck_agreement_drafts_revision CHECK (revision >= 1),
            CONSTRAINT ck_agreement_drafts_amends CHECK (amends_version_id IS NULL OR agreement_id IS NOT NULL),
            CONSTRAINT ck_agreement_drafts_approved CHECK ((status = 'approved') = (resulting_version_id IS NOT NULL)),
            CONSTRAINT ck_agreement_drafts_rejected CHECK (status <> 'rejected' OR rejection_reason IS NOT NULL),
            CONSTRAINT ck_agreement_drafts_rejection_reason CHECK (char_length(rejection_reason) BETWEEN 1 AND 2000),
            CONSTRAINT ck_agreement_drafts_review_comment CHECK (char_length(review_comment) BETWEEN 1 AND 2000)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_agreement_drafts_tenant_id_status ON agreement_drafts (tenant_id, status, id)"
    )
    op.execute(
        """
        CREATE TABLE agreement_draft_revisions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            draft_id uuid NOT NULL,
            revision integer NOT NULL,
            payload jsonb NOT NULL,
            edited_by uuid NOT NULL,
            change_summary text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_agreement_draft_revisions PRIMARY KEY (id),
            CONSTRAINT uq_agreement_draft_revisions_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_agreement_draft_revisions_tenant_id_draft_id_revision UNIQUE (tenant_id, draft_id, revision),
            CONSTRAINT fk_agreement_draft_revisions_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreement_draft_revisions_agreement_drafts FOREIGN KEY (tenant_id, draft_id)
                REFERENCES agreement_drafts (tenant_id, id),
            CONSTRAINT fk_agreement_draft_revisions_users FOREIGN KEY (edited_by) REFERENCES users (id),
            CONSTRAINT ck_agreement_draft_revisions_revision CHECK (revision >= 1),
            CONSTRAINT ck_agreement_draft_revisions_change_summary CHECK (char_length(change_summary) <= 500)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE agreement_versions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            agreement_id uuid NOT NULL,
            version_number integer NOT NULL,
            status text NOT NULL,
            valid_from date NOT NULL,
            valid_to date NOT NULL,
            valid_range daterange GENERATED ALWAYS AS (daterange(valid_from, valid_to, '[]')) STORED,
            rules jsonb NOT NULL,
            rules_schema_version integer NOT NULL,
            priority integer NOT NULL DEFAULT 0,
            source text NOT NULL,
            draft_id uuid,
            source_file_id uuid,
            derived_from_version_id uuid,
            supersedes_version_id uuid,
            superseded_by_version_id uuid,
            content_hash text NOT NULL,
            created_by uuid NOT NULL,
            approved_by uuid NOT NULL,
            activated_at timestamptz NOT NULL,
            superseded_at timestamptz,
            created_txid bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_agreement_versions PRIMARY KEY (id),
            CONSTRAINT uq_agreement_versions_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_agreement_versions_tenant_id_agreement_id_version_number
                UNIQUE (tenant_id, agreement_id, version_number),
            CONSTRAINT fk_agreement_versions_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreement_versions_agreements FOREIGN KEY (tenant_id, agreement_id)
                REFERENCES agreements (tenant_id, id),
            CONSTRAINT fk_agreement_versions_agreement_drafts FOREIGN KEY (tenant_id, draft_id)
                REFERENCES agreement_drafts (tenant_id, id),
            CONSTRAINT fk_agreement_versions_file_objects FOREIGN KEY (tenant_id, source_file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_agreement_versions_derived_from FOREIGN KEY (tenant_id, derived_from_version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_agreement_versions_supersedes FOREIGN KEY (tenant_id, supersedes_version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_agreement_versions_superseded_by FOREIGN KEY (tenant_id, superseded_by_version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_agreement_versions_created_by FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT fk_agreement_versions_approved_by FOREIGN KEY (approved_by) REFERENCES users (id),
            CONSTRAINT ck_agreement_versions_version_number CHECK (version_number >= 1),
            CONSTRAINT ck_agreement_versions_status CHECK (status IN ('active', 'superseded', 'expired', 'cancelled')),
            CONSTRAINT ck_agreement_versions_valid_range CHECK (valid_to >= valid_from),
            CONSTRAINT ck_agreement_versions_rules CHECK (jsonb_typeof(rules) = 'object'),
            CONSTRAINT ck_agreement_versions_rules_schema_version CHECK (rules_schema_version >= 1),
            CONSTRAINT ck_agreement_versions_source CHECK (source IN {SOURCES}),
            CONSTRAINT ck_agreement_versions_content_hash CHECK (content_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_agreement_versions_superseded CHECK (
                (status = 'superseded') = (superseded_at IS NOT NULL)),
            CONSTRAINT ck_agreement_versions_superseded_by CHECK (
                superseded_by_version_id IS NULL OR status = 'superseded'),
            CONSTRAINT ex_agreement_versions_no_overlap EXCLUDE USING gist
                (tenant_id WITH =, agreement_id WITH =, valid_range WITH &&)
                WHERE (status IN ('active', 'expired'))
        )
        """
    )
    op.execute(
        "ALTER TABLE agreement_drafts ADD CONSTRAINT fk_agreement_drafts_amends_version "
        "FOREIGN KEY (tenant_id, amends_version_id) REFERENCES agreement_versions (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE agreement_drafts ADD CONSTRAINT fk_agreement_drafts_resulting_version "
        "FOREIGN KEY (tenant_id, resulting_version_id) REFERENCES agreement_versions (tenant_id, id)"
    )
    op.execute(
        f"""
        CREATE TABLE agreement_version_items (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            version_id uuid NOT NULL,
            line_number integer NOT NULL,
            product_id uuid NOT NULL,
            customer_id uuid,
            reference_cost numeric(18,6),
            deviated_cost numeric(18,6),
            sell_price numeric(18,6),
            flat_amount numeric(18,6),
            rate numeric(9,6),
            claim_uom text,
            max_quantity numeric(18,6),
            CONSTRAINT pk_agreement_version_items PRIMARY KEY (id),
            CONSTRAINT uq_agreement_version_items_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_agreement_version_items_version_product_customer
                UNIQUE NULLS NOT DISTINCT (tenant_id, version_id, product_id, customer_id),
            CONSTRAINT fk_agreement_version_items_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreement_version_items_agreement_versions FOREIGN KEY (tenant_id, version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_agreement_version_items_products FOREIGN KEY (tenant_id, product_id)
                REFERENCES products (tenant_id, id),
            CONSTRAINT fk_agreement_version_items_customers FOREIGN KEY (tenant_id, customer_id)
                REFERENCES customers (tenant_id, id),
            CONSTRAINT ck_agreement_version_items_line_number CHECK (line_number >= 1),
            CONSTRAINT ck_agreement_version_items_costs CHECK (
                reference_cost >= 0 AND deviated_cost >= 0 AND sell_price >= 0 AND flat_amount >= 0),
            CONSTRAINT ck_agreement_version_items_rate CHECK (rate {RATE}),
            CONSTRAINT ck_agreement_version_items_max_quantity CHECK (max_quantity > 0),
            CONSTRAINT ck_agreement_version_items_claim_uom CHECK (claim_uom {UOM_CODE})
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_agreement_version_items_tenant_id_version_id "
        "ON agreement_version_items (tenant_id, version_id, line_number)"
    )
    op.execute(
        "CREATE INDEX ix_agreement_version_items_tenant_id_product_id ON agreement_version_items (tenant_id, product_id)"
    )
    op.execute(
        """
        CREATE TABLE agreement_version_customers (
            tenant_id uuid NOT NULL,
            version_id uuid NOT NULL,
            customer_id uuid NOT NULL,
            scope text NOT NULL,
            CONSTRAINT pk_agreement_version_customers PRIMARY KEY (version_id, customer_id),
            CONSTRAINT fk_agreement_version_customers_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreement_version_customers_agreement_versions FOREIGN KEY (tenant_id, version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_agreement_version_customers_customers FOREIGN KEY (tenant_id, customer_id)
                REFERENCES customers (tenant_id, id),
            CONSTRAINT ck_agreement_version_customers_scope CHECK (scope IN ('include', 'exclude'))
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE agreement_version_tiers (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            version_id uuid NOT NULL,
            tier_number integer NOT NULL,
            lower_bound numeric(18,2) NOT NULL,
            rate numeric(9,6),
            flat_amount numeric(18,4),
            CONSTRAINT pk_agreement_version_tiers PRIMARY KEY (id),
            CONSTRAINT uq_agreement_version_tiers_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_agreement_version_tiers_tenant_id_version_id_tier_number
                UNIQUE (tenant_id, version_id, tier_number),
            CONSTRAINT fk_agreement_version_tiers_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_agreement_version_tiers_agreement_versions FOREIGN KEY (tenant_id, version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT ck_agreement_version_tiers_tier_number CHECK (tier_number >= 1),
            CONSTRAINT ck_agreement_version_tiers_lower_bound CHECK (lower_bound >= 0),
            CONSTRAINT ck_agreement_version_tiers_rate CHECK (rate {RATE}),
            CONSTRAINT ck_agreement_version_tiers_flat_amount CHECK (flat_amount >= 0)
        )
        """
    )

    _version_triggers()
    for table in TENANT_TABLES:
        enable_tenant_rls(table)
    for table in CHILD_TABLES:
        make_append_only(table)
        op.execute(
            f"CREATE TRIGGER trg_{table}_parent_open BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION fn_agreement_version_child_insert()"
        )
    make_append_only("agreement_draft_revisions")
    for table in ("agreements", "agreement_drafts"):
        op.execute(f"REVOKE DELETE, TRUNCATE ON {table} FROM rebate_app")


def _version_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION fn_agreement_version_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF NEW.status NOT IN ('active', 'expired') THEN
                RAISE EXCEPTION 'agreement_versions: a version is created active or expired, not %', NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            NEW.created_txid := pg_current_xact_id()::text::bigint;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_agreement_version_update() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            -- valid_range is generated (NULL in NEW during BEFORE triggers); it follows valid_from/valid_to
            IF (to_jsonb(NEW) - 'status' - 'superseded_at' - 'superseded_by_version_id' - 'valid_range')
               IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'superseded_at' - 'superseded_by_version_id' - 'valid_range') THEN
                RAISE EXCEPTION 'agreement_versions: approved terms are immutable; only status, superseded_at '
                    'and superseded_by_version_id may change' USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'active' AND NEW.status IN ('superseded', 'expired', 'cancelled'))
                OR (OLD.status = 'expired' AND NEW.status IN ('superseded', 'cancelled'))) THEN
                RAISE EXCEPTION 'agreement_versions: status % cannot become %', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
                RAISE EXCEPTION 'agreement_versions: superseded_at is set once' USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.superseded_by_version_id IS NOT NULL
               AND NEW.superseded_by_version_id IS DISTINCT FROM OLD.superseded_by_version_id THEN
                RAISE EXCEPTION 'agreement_versions: superseded_by_version_id is set once'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_agreement_version_child_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE
            txid bigint;
        BEGIN
            SELECT v.created_txid INTO txid FROM public.agreement_versions v
            WHERE v.tenant_id = NEW.tenant_id AND v.id = NEW.version_id;
            IF NOT FOUND THEN
                RETURN NEW;  -- not visible (other tenant / unknown): RLS WITH CHECK or the FK refuses the row
            END IF;
            IF txid <> pg_current_xact_id()::text::bigint THEN
                RAISE EXCEPTION '%: the terms of an approved agreement version are immutable', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_agreement_versions_insert BEFORE INSERT ON agreement_versions "
        "FOR EACH ROW EXECUTE FUNCTION fn_agreement_version_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_agreement_versions_update BEFORE UPDATE ON agreement_versions "
        "FOR EACH ROW EXECUTE FUNCTION fn_agreement_version_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_agreement_versions_no_delete BEFORE DELETE ON agreement_versions "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_agreement_versions_no_truncate BEFORE TRUNCATE ON agreement_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON agreement_versions FROM rebate_app")
    op.execute(
        "GRANT UPDATE (status, superseded_at, superseded_by_version_id) ON agreement_versions TO rebate_app"
    )
