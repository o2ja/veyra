"""S3.1 master data: manufacturers (+settings), reason codes (global, seeded), reason-code mappings,
customers (+aliases, parent), branches, products (+aliases), UoM conversions, unmapped-entity queue.

Every tenant table: tenant_id, UNIQUE (tenant_id, id) where it has an id, composite FKs, RLS via the
shared helper (DATABASE_SCHEMA.md §2, §3.3). Identifiers that are matched against import files (SKU,
alias, UoM) are stored in their canonical upper-case form (CHECK = upper(...)), see the normalisers.

Revision ID: 20261111_0900_masterdata
Revises: 20261110_1200_s2_review_fixes
"""

import sqlalchemy as sa
from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261111_0900_masterdata"
down_revision = "20261110_1200_s2_review_fixes"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "manufacturers",
    "manufacturer_settings",
    "reason_code_mappings",
    "customers",
    "customer_aliases",
    "branches",
    "products",
    "product_aliases",
    "uom_conversions",
    "unmapped_entities",
)

# BUSINESS_LOGIC.md §10 (controlled list). (code, label, category)
REASON_CODES = (
    ("rejected_agreement_expired", "Rejected: agreement expired", "rejected"),
    ("rejected_customer_ineligible", "Rejected: customer not eligible", "rejected"),
    ("rejected_product_ineligible", "Rejected: product not eligible", "rejected"),
    ("rejected_price_mismatch", "Rejected: price mismatch", "rejected"),
    ("rejected_duplicate_claim", "Rejected: duplicate claim", "rejected"),
    ("rejected_outside_claim_window", "Rejected: outside claim window", "rejected"),
    ("rejected_missing_documentation", "Rejected: missing documentation", "rejected"),
    ("rejected_quantity_exceeds_authorization", "Rejected: quantity exceeds authorization", "rejected"),
    ("rejected_uom_mismatch", "Rejected: unit of measure mismatch", "rejected"),
    ("rejected_other", "Rejected: other reason", "rejected"),
    ("short_paid_unexplained", "Short paid without explanation", "short_paid"),
    ("unpaid_no_remittance", "Unpaid: no remittance received", "unpaid"),
    ("overpaid", "Overpaid", "overpaid"),
    ("unmatched_payment", "Payment without a matching claim", "unmatched"),
    ("source_changed", "Source data changed after claiming", "source_changed"),
    ("unknown", "Unknown or unmapped manufacturer reason", "unknown"),
)

UOM_CODE = "~ '^[A-Z0-9]{1,10}$'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE manufacturers (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            name text NOT NULL,
            code text NOT NULL,
            external_id text,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_manufacturers PRIMARY KEY (id),
            CONSTRAINT uq_manufacturers_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_manufacturers_tenant_id_code UNIQUE (tenant_id, code),
            CONSTRAINT fk_manufacturers_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_manufacturers_name CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_manufacturers_code CHECK (code ~ '^[A-Z0-9][A-Z0-9_.-]{0,49}$'),
            CONSTRAINT ck_manufacturers_external_id CHECK (char_length(external_id) <= 100)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE manufacturer_settings (
            tenant_id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            claim_window_days integer,
            claim_window_basis text,
            dispute_window_days integer,
            dispute_window_basis text,
            allow_negative_claims boolean NOT NULL DEFAULT false,
            conflict_policy text NOT NULL DEFAULT 'flag',
            variance_tolerance numeric(18,4) NOT NULL DEFAULT 0.01,
            payment_lag_days_expected integer,
            notes text,
            revision integer NOT NULL DEFAULT 1,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_manufacturer_settings PRIMARY KEY (manufacturer_id),
            CONSTRAINT fk_manufacturer_settings_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_manufacturer_settings_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_manufacturer_settings_claim_window_days CHECK (claim_window_days BETWEEN 1 AND 3650),
            CONSTRAINT ck_manufacturer_settings_claim_window_basis
                CHECK (claim_window_basis IN ('invoice_date', 'ship_date', 'period_end')),
            CONSTRAINT ck_manufacturer_settings_dispute_window_days CHECK (dispute_window_days BETWEEN 1 AND 3650),
            CONSTRAINT ck_manufacturer_settings_dispute_window_basis
                CHECK (dispute_window_basis IN ('remittance_date', 'submission_date')),
            CONSTRAINT ck_manufacturer_settings_window_pairs CHECK (
                (claim_window_days IS NULL) = (claim_window_basis IS NULL)
                AND (dispute_window_days IS NULL) = (dispute_window_basis IS NULL)),
            CONSTRAINT ck_manufacturer_settings_conflict_policy CHECK (conflict_policy IN ('flag', 'priority')),
            CONSTRAINT ck_manufacturer_settings_variance_tolerance CHECK (variance_tolerance >= 0),
            CONSTRAINT ck_manufacturer_settings_payment_lag CHECK (payment_lag_days_expected BETWEEN 0 AND 3650),
            CONSTRAINT ck_manufacturer_settings_notes CHECK (char_length(notes) <= 2000),
            CONSTRAINT ck_manufacturer_settings_revision CHECK (revision >= 1)
        )
        """
    )

    # Global reference list (DATABASE_SCHEMA.md §2: no RLS). Seeded here, never written by the app.
    op.execute(
        """
        CREATE TABLE reason_codes (
            code text NOT NULL,
            label text NOT NULL,
            category text NOT NULL,
            description text,
            CONSTRAINT pk_reason_codes PRIMARY KEY (code),
            CONSTRAINT ck_reason_codes_code CHECK (code ~ '^[a-z][a-z0-9_]{1,62}$')
        )
        """
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO reason_codes (code, label, category) VALUES (:c, :l, :k) ON CONFLICT (code) DO NOTHING"
        ),
        [{"c": c, "l": label, "k": k} for c, label, k in REASON_CODES],
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON reason_codes FROM rebate_app")

    op.execute(
        """
        CREATE TABLE reason_code_mappings (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            external_code text NOT NULL,
            external_text text,
            reason_code text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_reason_code_mappings PRIMARY KEY (id),
            CONSTRAINT uq_reason_code_mappings_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_reason_code_mappings_tenant_id_manufacturer_id_external_code
                UNIQUE (tenant_id, manufacturer_id, external_code),
            CONSTRAINT fk_reason_code_mappings_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_reason_code_mappings_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_reason_code_mappings_reason_codes FOREIGN KEY (reason_code) REFERENCES reason_codes (code),
            CONSTRAINT ck_reason_code_mappings_external_code CHECK (char_length(external_code) BETWEEN 1 AND 50),
            CONSTRAINT ck_reason_code_mappings_external_text CHECK (char_length(external_text) <= 500)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE customers (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            name text NOT NULL,
            code text,
            external_id text,
            customer_type text NOT NULL DEFAULT 'other',
            parent_customer_id uuid,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_customers PRIMARY KEY (id),
            CONSTRAINT uq_customers_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_customers_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_customers_parent FOREIGN KEY (tenant_id, parent_customer_id)
                REFERENCES customers (tenant_id, id),
            CONSTRAINT ck_customers_name CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_customers_code CHECK (code = upper(code) AND char_length(code) BETWEEN 1 AND 50),
            CONSTRAINT ck_customers_external_id CHECK (char_length(external_id) <= 100),
            CONSTRAINT ck_customers_customer_type
                CHECK (customer_type IN ('contractor', 'end_user', 'branch', 'other')),
            CONSTRAINT ck_customers_not_own_parent CHECK (parent_customer_id <> id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_customers_tenant_id_code ON customers (tenant_id, code) WHERE code IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_customers_name_trgm ON customers USING gin (name gin_trgm_ops)")

    op.execute(
        """
        CREATE TABLE customer_aliases (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            customer_id uuid NOT NULL,
            alias text NOT NULL,
            source text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_customer_aliases PRIMARY KEY (id),
            CONSTRAINT uq_customer_aliases_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_customer_aliases_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_customer_aliases_customers FOREIGN KEY (tenant_id, customer_id)
                REFERENCES customers (tenant_id, id),
            CONSTRAINT ck_customer_aliases_alias CHECK (alias = upper(alias) AND char_length(alias) BETWEEN 1 AND 200),
            CONSTRAINT ck_customer_aliases_source CHECK (char_length(source) <= 50)
        )
        """
    )
    # alias is stored upper-case (CHECK), so this is the documented UNIQUE (tenant_id, lower(alias))
    op.execute(
        "CREATE UNIQUE INDEX uq_customer_aliases_tenant_id_alias ON customer_aliases (tenant_id, alias)"
    )
    op.execute(
        "CREATE INDEX ix_customer_aliases_tenant_id_customer_id ON customer_aliases (tenant_id, customer_id)"
    )

    op.execute(
        """
        CREATE TABLE branches (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            name text NOT NULL,
            code text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_branches PRIMARY KEY (id),
            CONSTRAINT uq_branches_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_branches_tenant_id_code UNIQUE (tenant_id, code),
            CONSTRAINT fk_branches_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_branches_name CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_branches_code CHECK (code = upper(code) AND char_length(code) BETWEEN 1 AND 50)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE products (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            sku text NOT NULL,
            description text,
            manufacturer_id uuid NOT NULL,
            manufacturer_part_number text,
            base_uom text NOT NULL,
            category text,
            list_cost numeric(18,6),
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_products PRIMARY KEY (id),
            CONSTRAINT uq_products_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_products_tenant_id_sku UNIQUE (tenant_id, sku),
            CONSTRAINT fk_products_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_products_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT ck_products_sku CHECK (sku = upper(sku) AND char_length(sku) BETWEEN 1 AND 100),
            CONSTRAINT ck_products_description CHECK (char_length(description) <= 500),
            CONSTRAINT ck_products_mpn CHECK (char_length(manufacturer_part_number) <= 100),
            CONSTRAINT ck_products_base_uom CHECK (base_uom {UOM_CODE}),
            CONSTRAINT ck_products_category CHECK (char_length(category) <= 100),
            CONSTRAINT ck_products_list_cost CHECK (list_cost >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_products_tenant_id_manufacturer_id ON products (tenant_id, manufacturer_id)")
    op.execute("CREATE INDEX ix_products_sku_trgm ON products USING gin (sku gin_trgm_ops)")

    op.execute(
        """
        CREATE TABLE product_aliases (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            product_id uuid NOT NULL,
            alias text NOT NULL,
            alias_type text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_product_aliases PRIMARY KEY (id),
            CONSTRAINT uq_product_aliases_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_product_aliases_tenant_id_alias_type_alias UNIQUE (tenant_id, alias_type, alias),
            CONSTRAINT fk_product_aliases_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_product_aliases_products FOREIGN KEY (tenant_id, product_id)
                REFERENCES products (tenant_id, id),
            CONSTRAINT ck_product_aliases_alias CHECK (alias = upper(alias) AND char_length(alias) BETWEEN 1 AND 100),
            CONSTRAINT ck_product_aliases_alias_type
                CHECK (alias_type IN ('erp_sku', 'manufacturer_part', 'upc', 'other'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_product_aliases_tenant_id_product_id ON product_aliases (tenant_id, product_id)"
    )

    op.execute(
        f"""
        CREATE TABLE uom_conversions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            product_id uuid,
            from_uom text NOT NULL,
            to_uom text NOT NULL,
            factor numeric(18,8) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_uom_conversions PRIMARY KEY (id),
            CONSTRAINT uq_uom_conversions_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_uom_conversions_tenant_id_product_id_from_uom_to_uom
                UNIQUE NULLS NOT DISTINCT (tenant_id, product_id, from_uom, to_uom),
            CONSTRAINT fk_uom_conversions_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_uom_conversions_products FOREIGN KEY (tenant_id, product_id)
                REFERENCES products (tenant_id, id),
            CONSTRAINT ck_uom_conversions_from_uom CHECK (from_uom {UOM_CODE}),
            CONSTRAINT ck_uom_conversions_to_uom CHECK (to_uom {UOM_CODE}),
            CONSTRAINT ck_uom_conversions_distinct CHECK (from_uom <> to_uom),
            CONSTRAINT ck_uom_conversions_factor CHECK (factor > 0)
        )
        """
    )

    # Customers/products seen in imports without a master match (FR-IM-09). The FK to the batch that
    # first saw the value is added with import_batches (next migration).
    op.execute(
        """
        CREATE TABLE unmapped_entities (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            entity_type text NOT NULL,
            external_value text NOT NULL,
            example_name text,
            status text NOT NULL DEFAULT 'open',
            seen_count integer NOT NULL DEFAULT 1,
            first_import_batch_id uuid,
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            resolved_entity_id uuid,
            resolved_by uuid,
            resolved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_unmapped_entities PRIMARY KEY (id),
            CONSTRAINT uq_unmapped_entities_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_unmapped_entities_tenant_id_entity_type_external_value
                UNIQUE (tenant_id, entity_type, external_value),
            CONSTRAINT fk_unmapped_entities_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_unmapped_entities_users FOREIGN KEY (resolved_by) REFERENCES users (id),
            CONSTRAINT ck_unmapped_entities_entity_type CHECK (entity_type IN ('customer', 'product')),
            CONSTRAINT ck_unmapped_entities_external_value
                CHECK (external_value = upper(external_value) AND char_length(external_value) BETWEEN 1 AND 200),
            CONSTRAINT ck_unmapped_entities_example_name CHECK (char_length(example_name) <= 200),
            CONSTRAINT ck_unmapped_entities_status CHECK (status IN ('open', 'resolved', 'ignored')),
            CONSTRAINT ck_unmapped_entities_seen_count CHECK (seen_count >= 1),
            CONSTRAINT ck_unmapped_entities_resolution CHECK (
                (status = 'resolved') = (resolved_entity_id IS NOT NULL)
                AND (status = 'open') = (resolved_at IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_unmapped_entities_tenant_id_status ON unmapped_entities (tenant_id, status, created_at)"
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)
