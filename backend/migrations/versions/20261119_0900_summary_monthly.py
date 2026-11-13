"""S10.1 summary_claims_monthly (DATABASE_SCHEMA.md §3.11): the dashboard's refreshed monthly figures.

Never the source of truth: the refresh job deletes and re-inserts a tenant's rows in one transaction from the claims,
reconciliation and recovery services, and every figure is reproducible by a drill-down query (tested). Extra columns
over the schema doc: ``id`` (the tenant-table convention; the natural key is the unique index with
``coalesce(branch_id, zero uuid)``), ``expiring`` (FR-RP-01 names it beside ``at_risk``) and ``as_of_date`` (the
business date the date-dependent ``at_risk``/``expiring`` were computed for). Forward-only like the rest of the chain.

Revision ID: 20261119_0900_summary_monthly
Revises: 20261118_0900_recovery_cases
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261119_0900_summary_monthly"
down_revision = "20261118_0900_recovery_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE summary_claims_monthly (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            branch_id uuid,
            month date NOT NULL,
            claimed numeric(18, 4) NOT NULL DEFAULT 0,
            collected numeric(18, 4) NOT NULL DEFAULT 0,
            short_paid numeric(18, 4) NOT NULL DEFAULT 0,
            recovered numeric(18, 4) NOT NULL DEFAULT 0,
            written_off numeric(18, 4) NOT NULL DEFAULT 0,
            at_risk numeric(18, 4) NOT NULL DEFAULT 0,
            expiring numeric(18, 4) NOT NULL DEFAULT 0,
            as_of_date date NOT NULL,
            refreshed_at timestamptz NOT NULL,
            CONSTRAINT pk_summary_claims_monthly PRIMARY KEY (id),
            CONSTRAINT uq_summary_claims_monthly_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_summary_claims_monthly_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_summary_claims_monthly_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_summary_claims_monthly_branches FOREIGN KEY (tenant_id, branch_id)
                REFERENCES branches (tenant_id, id),
            CONSTRAINT ck_summary_claims_monthly_month CHECK (month = date_trunc('month', month)::date)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_summary_claims_monthly_key ON summary_claims_monthly "
        "(tenant_id, manufacturer_id, coalesce(branch_id, '00000000-0000-0000-0000-000000000000'), month)"
    )
    op.execute(
        "CREATE INDEX ix_summary_claims_monthly_tenant_id_month ON summary_claims_monthly (tenant_id, month)"
    )
    enable_tenant_rls("summary_claims_monthly")
