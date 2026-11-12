"""S7.1 claim_export_templates: per-manufacturer column layouts for claim files (FR-CB-03).

DATABASE_SCHEMA.md §3.8. Tenant table: composite FKs, RLS ENABLE + FORCE via the shared helper. Templates are
never deleted (history of what was exported stays explainable): ``active = false`` retires one. ``revision`` backs
``If-Match`` (API_SPECIFICATION.md §4). At most one template name per manufacturer.

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261115_0900_claim_templates
Revises: 20261114_0900_calc_runs
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261115_0900_claim_templates"
down_revision = "20261114_0900_calc_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE claim_export_templates (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            name text NOT NULL,
            format text NOT NULL,
            columns jsonb NOT NULL,
            options jsonb NOT NULL DEFAULT '{}',
            active boolean NOT NULL DEFAULT true,
            revision integer NOT NULL DEFAULT 1,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_claim_export_templates PRIMARY KEY (id),
            CONSTRAINT uq_claim_export_templates_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_claim_export_templates_name UNIQUE (tenant_id, manufacturer_id, name),
            CONSTRAINT fk_claim_export_templates_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_claim_export_templates_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_claim_export_templates_users FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_claim_export_templates_format CHECK (format IN ('csv', 'xlsx')),
            CONSTRAINT ck_claim_export_templates_name CHECK (char_length(name) BETWEEN 1 AND 100),
            CONSTRAINT ck_claim_export_templates_columns
                CHECK (jsonb_typeof(columns) = 'array' AND jsonb_array_length(columns) BETWEEN 1 AND 100),
            CONSTRAINT ck_claim_export_templates_options CHECK (jsonb_typeof(options) = 'object'),
            CONSTRAINT ck_claim_export_templates_revision CHECK (revision >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_claim_export_templates_tenant_id_manufacturer_id ON claim_export_templates "
        "(tenant_id, manufacturer_id)"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_export_templates_no_delete BEFORE DELETE ON claim_export_templates "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_export_templates_no_truncate BEFORE TRUNCATE ON claim_export_templates "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE DELETE, TRUNCATE ON claim_export_templates FROM rebate_app")
    enable_tenant_rls("claim_export_templates")
