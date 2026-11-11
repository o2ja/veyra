"""S5.4 calculation runs: calculation_runs, calculation_results, rebate_accrual_entries, claim_adjustments.

DATABASE_SCHEMA.md §3.7-§3.8; BUSINESS_LOGIC.md §4-§9, §12-§13. Every table: tenant_id, composite FKs, RLS ENABLE +
FORCE via the shared helper.

- calculation_runs: pinned inputs are immutable. After creation only the run's own bookkeeping may change
  (status along queued -> running -> completed|failed, agreement_version_ids/input_checksum/totals/error and the
  timestamps) and terminal runs never change again. At most one queued/running run per (tenant, scope_hash).
- calculation_results, rebate_accrual_entries: append-only (trigger + privileges).
- claim_adjustments: no DELETE/TRUNCATE; amounts and links are immutable, only the approval workflow columns move.
  ``claim_line_id`` / ``variance_case_id`` are plain uuids until S7/S9 create those tables (FKs are added there).
- manufacturer_settings.claim_window_basis: ``month_end`` (SPA lines) joins ``period_end`` (rebates only) in the
  CHECK; owner decision 2026-11-11. Existing ``period_end`` rows become ``month_end`` (their old meaning).

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261114_0900_calc_runs
Revises: 20261113_0900_extraction
"""

from alembic import op

from migrations._immutable import make_append_only
from migrations._rls import enable_tenant_rls

revision = "20261114_0900_calc_runs"
down_revision = "20261113_0900_extraction"
branch_labels = None
depends_on = None

PERIOD_END_TO_MONTH_END = (
    "UPDATE manufacturer_settings SET claim_window_basis = 'month_end', revision = revision + 1, "
    "updated_at = now() WHERE claim_window_basis = 'period_end'"
)
# manufacturer_settings has FORCE ROW LEVEL SECURITY: the owner role is subject to the tenant policy too (no tenant
# is set in a migration, so a plain UPDATE would match no row). The owner bypasses only while FORCE is off.
PERIOD_END_STEPS = (
    "ALTER TABLE manufacturer_settings NO FORCE ROW LEVEL SECURITY",
    PERIOD_END_TO_MONTH_END,
    "ALTER TABLE manufacturer_settings FORCE ROW LEVEL SECURITY",
)
TENANT_TABLES = ("calculation_runs", "calculation_results", "rebate_accrual_entries", "claim_adjustments")
RUN_BOOKKEEPING = "'status' - 'agreement_version_ids' - 'input_checksum' - 'totals' - 'error' - 'started_at' - 'completed_at'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE calculation_runs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            scope jsonb NOT NULL,
            scope_hash text NOT NULL,
            as_of timestamptz NOT NULL,
            data_cutoff timestamptz NOT NULL,
            run_date date NOT NULL,
            engine_version text NOT NULL,
            config_hash text NOT NULL,
            agreement_version_ids uuid[] NOT NULL DEFAULT '{}',
            input_checksum text,
            status text NOT NULL DEFAULT 'queued',
            totals jsonb,
            error jsonb,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            completed_at timestamptz,
            CONSTRAINT pk_calculation_runs PRIMARY KEY (id),
            CONSTRAINT uq_calculation_runs_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_calculation_runs_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_calculation_runs_users FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_calculation_runs_status
                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            CONSTRAINT ck_calculation_runs_completed CHECK (
                status <> 'completed'
                OR (totals IS NOT NULL AND input_checksum IS NOT NULL AND completed_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_calculation_runs_active_scope ON calculation_runs (tenant_id, scope_hash) "
        "WHERE status IN ('queued', 'running')"
    )
    op.execute(
        f"""
        CREATE FUNCTION fn_calculation_run_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - {RUN_BOOKKEEPING}) IS DISTINCT FROM (to_jsonb(OLD) - {RUN_BOOKKEEPING}) THEN
                RAISE EXCEPTION 'calculation_runs: pinned inputs are immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status IN ('completed', 'failed', 'cancelled') THEN
                RAISE EXCEPTION 'calculation_runs: a % run never changes', OLD.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status <> OLD.status AND NOT (
                (OLD.status = 'queued' AND NEW.status IN ('running', 'failed', 'cancelled'))
                OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed', 'cancelled'))) THEN
                RAISE EXCEPTION 'calculation_runs: illegal status transition % -> %', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_calculation_runs_update BEFORE UPDATE ON calculation_runs "
        "FOR EACH ROW EXECUTE FUNCTION fn_calculation_run_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_calculation_runs_no_delete BEFORE DELETE ON calculation_runs "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_calculation_runs_no_truncate BEFORE TRUNCATE ON calculation_runs "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON calculation_runs FROM rebate_app")
    op.execute(
        "GRANT UPDATE (status, agreement_version_ids, input_checksum, totals, error, started_at, completed_at) "
        "ON calculation_runs TO rebate_app"
    )

    op.execute(
        """
        CREATE TABLE calculation_results (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            run_id uuid NOT NULL,
            sales_document_line_id uuid NOT NULL,
            document_number text NOT NULL,
            line_number integer NOT NULL,
            invoice_date date NOT NULL,
            manufacturer_id uuid,
            agreement_id uuid,
            agreement_version_id uuid,
            status text NOT NULL,
            exception_code text,
            eligible_quantity numeric(18, 6),
            excess_quantity numeric(18, 6),
            claim_uom text,
            claim_unit_amount numeric(18, 6),
            claim_amount numeric(18, 4) NOT NULL DEFAULT 0,
            flags text[] NOT NULL DEFAULT '{}',
            trace jsonb NOT NULL,
            inputs_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_calculation_results PRIMARY KEY (id),
            CONSTRAINT uq_calculation_results_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_calculation_results_run_line UNIQUE (tenant_id, run_id, sales_document_line_id),
            CONSTRAINT fk_calculation_results_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_calculation_results_calculation_runs FOREIGN KEY (tenant_id, run_id)
                REFERENCES calculation_runs (tenant_id, id),
            CONSTRAINT fk_calculation_results_sales_document_lines
                FOREIGN KEY (tenant_id, sales_document_line_id) REFERENCES sales_document_lines (tenant_id, id),
            CONSTRAINT fk_calculation_results_agreement_versions FOREIGN KEY (tenant_id, agreement_version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_calculation_results_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_calculation_results_agreements FOREIGN KEY (tenant_id, agreement_id)
                REFERENCES agreements (tenant_id, id),
            CONSTRAINT ck_calculation_results_status
                CHECK (status IN ('eligible', 'exception', 'netted', 'adjustment', 'not_applicable')),
            CONSTRAINT ck_calculation_results_exception_code CHECK (
                (status = 'exception' AND exception_code IS NOT NULL)
                OR status = 'not_applicable'
                OR (status NOT IN ('exception', 'not_applicable') AND exception_code IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_calculation_results_tenant_id_run_id_status ON calculation_results "
        "(tenant_id, run_id, status, id)"
    )
    op.execute(
        "CREATE INDEX ix_calculation_results_tenant_id_run_id_id ON calculation_results (tenant_id, run_id, id)"
    )
    op.execute(
        "CREATE INDEX ix_calculation_results_tenant_id_sales_document_line_id ON calculation_results "
        "(tenant_id, sales_document_line_id)"
    )
    op.execute(
        "CREATE INDEX ix_calculation_results_tenant_id_agreement_version_id ON calculation_results "
        "(tenant_id, agreement_version_id)"
    )

    op.execute(
        """
        CREATE TABLE rebate_accrual_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            run_id uuid NOT NULL,
            agreement_id uuid NOT NULL,
            agreement_version_id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            external_number text NOT NULL,
            agreement_name text NOT NULL,
            tiers jsonb NOT NULL,
            period_start date NOT NULL,
            period_end date NOT NULL,
            as_of_date date NOT NULL,
            cumulative_qualifying numeric(18, 4) NOT NULL,
            cumulative_payout_base numeric(18, 4) NOT NULL,
            tier_number integer,
            tier_rate numeric(9, 6),
            cumulative_earned numeric(18, 4) NOT NULL,
            prior_cumulative_accrued numeric(18, 4) NOT NULL,
            delta numeric(18, 4) NOT NULL,
            claim_deadline date,
            flags text[] NOT NULL DEFAULT '{}',
            trace jsonb NOT NULL,
            inputs_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_rebate_accrual_entries PRIMARY KEY (id),
            CONSTRAINT uq_rebate_accrual_entries_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_rebate_accrual_entries_run_agreement_period
                UNIQUE (tenant_id, run_id, agreement_id, period_start),
            CONSTRAINT fk_rebate_accrual_entries_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_rebate_accrual_entries_calculation_runs FOREIGN KEY (tenant_id, run_id)
                REFERENCES calculation_runs (tenant_id, id),
            CONSTRAINT fk_rebate_accrual_entries_agreements FOREIGN KEY (tenant_id, agreement_id)
                REFERENCES agreements (tenant_id, id),
            CONSTRAINT fk_rebate_accrual_entries_agreement_versions FOREIGN KEY (tenant_id, agreement_version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT fk_rebate_accrual_entries_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT ck_rebate_accrual_entries_period CHECK (period_end >= period_start)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rebate_accrual_entries_tenant_id_agreement_id_period_start "
        "ON rebate_accrual_entries (tenant_id, agreement_id, period_start)"
    )

    op.execute(
        """
        CREATE TABLE claim_adjustments (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            adjustment_type text NOT NULL,
            claim_line_id uuid,
            calculation_result_id uuid,
            variance_case_id uuid,
            run_id uuid,
            amount numeric(18, 4) NOT NULL,
            reason text NOT NULL,
            status text NOT NULL DEFAULT 'proposed',
            included_in_batch_id uuid,
            proposed_by uuid,
            approved_by uuid,
            approved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_claim_adjustments PRIMARY KEY (id),
            CONSTRAINT uq_claim_adjustments_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_claim_adjustments_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_claim_adjustments_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_claim_adjustments_calculation_results FOREIGN KEY (tenant_id, calculation_result_id)
                REFERENCES calculation_results (tenant_id, id),
            CONSTRAINT fk_claim_adjustments_calculation_runs FOREIGN KEY (tenant_id, run_id)
                REFERENCES calculation_runs (tenant_id, id),
            CONSTRAINT fk_claim_adjustments_users_proposed FOREIGN KEY (proposed_by) REFERENCES users (id),
            CONSTRAINT fk_claim_adjustments_users_approved FOREIGN KEY (approved_by) REFERENCES users (id),
            CONSTRAINT ck_claim_adjustments_type CHECK (adjustment_type IN
                ('return_reversal', 'agreement_change', 'manual_correction', 'rebate_clawback', 'recovery_reclaim')),
            CONSTRAINT ck_claim_adjustments_status CHECK (status IN ('proposed', 'approved', 'included', 'rejected')),
            CONSTRAINT ck_claim_adjustments_reason CHECK (char_length(reason) BETWEEN 1 AND 1000)
        )
        """
    )
    # one proposal per (claim line, type, run): proposing twice from the same run is a no-op
    op.execute(
        "CREATE UNIQUE INDEX uq_claim_adjustments_proposal ON claim_adjustments "
        "(tenant_id, claim_line_id, adjustment_type, run_id) WHERE claim_line_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_claim_adjustments_tenant_id_status ON claim_adjustments (tenant_id, status, id)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_claim_adjustment_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status' - 'included_in_batch_id' - 'approved_by' - 'approved_at')
               IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'included_in_batch_id' - 'approved_by' - 'approved_at') THEN
                RAISE EXCEPTION 'claim_adjustments: amount, type, links and reason are immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status = OLD.status AND (NEW.approved_by IS DISTINCT FROM OLD.approved_by
                OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                OR NEW.included_in_batch_id IS DISTINCT FROM OLD.included_in_batch_id) THEN
                RAISE EXCEPTION 'claim_adjustments: approval columns change only with the status'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status <> OLD.status AND NOT (
                (OLD.status = 'proposed' AND NEW.status IN ('approved', 'rejected'))
                OR (OLD.status = 'approved' AND NEW.status IN ('included', 'rejected'))) THEN
                RAISE EXCEPTION 'claim_adjustments: illegal status transition % -> %', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_claim_adjustments_update BEFORE UPDATE ON claim_adjustments "
        "FOR EACH ROW EXECUTE FUNCTION fn_claim_adjustment_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_adjustments_no_delete BEFORE DELETE ON claim_adjustments "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_adjustments_no_truncate BEFORE TRUNCATE ON claim_adjustments "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON claim_adjustments FROM rebate_app")
    op.execute(
        "GRANT UPDATE (status, included_in_batch_id, approved_by, approved_at) ON claim_adjustments TO rebate_app"
    )

    make_append_only("calculation_results")
    make_append_only("rebate_accrual_entries")
    for table in TENANT_TABLES:
        enable_tenant_rls(table)

    op.execute(
        "ALTER TABLE manufacturer_settings DROP CONSTRAINT ck_manufacturer_settings_claim_window_basis"
    )
    op.execute(
        "ALTER TABLE manufacturer_settings ADD CONSTRAINT ck_manufacturer_settings_claim_window_basis "
        "CHECK (claim_window_basis IN ('invoice_date', 'ship_date', 'month_end', 'period_end'))"
    )
    # the old 'period_end' meant the month end of the line's claim date (SPA lines): keep the meaning, new name;
    # the revision moves on so open settings forms see the change
    # Not audited: a migration has no actor or tenant context (audit_log is tenant-scoped, written by the app);
    # the change is recorded here, in the revision's docstring and in docs/reports/notes/c1-s5-runs.md.
    for statement in PERIOD_END_STEPS:
        op.execute(statement)
