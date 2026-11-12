"""S7.1 claim_lines: the claimed amount per source line, frozen when its batch is submitted.

DATABASE_SCHEMA.md §3.8, §7. Tenant table: composite FKs, RLS ENABLE + FORCE via the shared helper.

- Partial unique indexes: a sales line / accrual entry has at most one *active* (``released_at IS NULL``) claim
  line (BUSINESS_LOGIC.md §9.4; 409 ``already_claimed`` in the API).
- Lines can be added only to a ``draft`` batch. After insert only ``frozen_at`` and ``released_at`` may be set, each
  once: ``frozen_at`` only while the parent batch is ``submitted``; ``released_at`` only while it is
  ``draft``/``approved``/``void`` (in practice the void transaction). A frozen line never changes or goes away.
- Never deleted (privileges revoked + trigger).
- ``claim_adjustments.claim_line_id`` gets its composite FK here (placeholder since S5).

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261115_0920_claim_lines
Revises: 20261115_0910_claim_batches
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261115_0920_claim_lines"
down_revision = "20261115_0910_claim_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE claim_lines (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            batch_id uuid NOT NULL,
            claim_reference text NOT NULL,
            source_kind text NOT NULL,
            sales_document_line_id uuid,
            rebate_accrual_entry_id uuid,
            calculation_result_id uuid NOT NULL,
            agreement_version_id uuid NOT NULL,
            quantity numeric(18, 6),
            unit_amount numeric(18, 6),
            claim_amount numeric(18, 4) NOT NULL,
            frozen_at timestamptz,
            released_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_claim_lines PRIMARY KEY (id),
            CONSTRAINT uq_claim_lines_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_claim_lines_claim_reference UNIQUE (tenant_id, claim_reference),
            CONSTRAINT fk_claim_lines_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_claim_lines_claim_batches FOREIGN KEY (tenant_id, batch_id)
                REFERENCES claim_batches (tenant_id, id),
            CONSTRAINT fk_claim_lines_sales_document_lines FOREIGN KEY (tenant_id, sales_document_line_id)
                REFERENCES sales_document_lines (tenant_id, id),
            CONSTRAINT fk_claim_lines_rebate_accrual_entries FOREIGN KEY (tenant_id, rebate_accrual_entry_id)
                REFERENCES rebate_accrual_entries (tenant_id, id),
            CONSTRAINT fk_claim_lines_calculation_results FOREIGN KEY (tenant_id, calculation_result_id)
                REFERENCES calculation_results (tenant_id, id),
            CONSTRAINT fk_claim_lines_agreement_versions FOREIGN KEY (tenant_id, agreement_version_id)
                REFERENCES agreement_versions (tenant_id, id),
            CONSTRAINT ck_claim_lines_source_kind CHECK (source_kind IN ('sales_line', 'rebate_accrual')),
            CONSTRAINT ck_claim_lines_sales_source
                CHECK ((source_kind = 'sales_line') = (sales_document_line_id IS NOT NULL)),
            CONSTRAINT ck_claim_lines_accrual_source
                CHECK ((source_kind = 'rebate_accrual') = (rebate_accrual_entry_id IS NOT NULL)),
            CONSTRAINT ck_claim_lines_amount CHECK (claim_amount >= 0),
            CONSTRAINT ck_claim_lines_reference CHECK (char_length(claim_reference) BETWEEN 1 AND 40)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_claim_lines_active_sales_line ON claim_lines (tenant_id, sales_document_line_id) "
        "WHERE released_at IS NULL AND sales_document_line_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_claim_lines_active_accrual_entry ON claim_lines "
        "(tenant_id, rebate_accrual_entry_id) WHERE released_at IS NULL AND rebate_accrual_entry_id IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_claim_lines_tenant_id_batch_id ON claim_lines (tenant_id, batch_id, id)")
    op.execute(
        "CREATE INDEX ix_claim_lines_tenant_id_calculation_result_id ON claim_lines (tenant_id, calculation_result_id)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_claim_line_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            batch_status text;
        BEGIN
            IF NEW.frozen_at IS NOT NULL OR NEW.released_at IS NOT NULL THEN
                RAISE EXCEPTION 'claim_lines: a new line is neither frozen nor released'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            SELECT status INTO batch_status FROM claim_batches WHERE tenant_id = NEW.tenant_id AND id = NEW.batch_id;
            IF batch_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'claim_lines: lines can only be added to a draft batch'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_claim_line_update() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            batch_status text;
        BEGIN
            IF OLD.frozen_at IS NOT NULL THEN
                RAISE EXCEPTION 'claim_lines: a frozen (submitted) line never changes'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - 'frozen_at' - 'released_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'frozen_at' - 'released_at') THEN
                RAISE EXCEPTION 'claim_lines: only frozen_at and released_at may be set'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            SELECT status INTO batch_status FROM claim_batches WHERE tenant_id = NEW.tenant_id AND id = NEW.batch_id;
            IF NEW.frozen_at IS NOT NULL THEN
                IF OLD.released_at IS NOT NULL OR NEW.released_at IS NOT NULL OR batch_status <> 'submitted' THEN
                    RAISE EXCEPTION 'claim_lines: a line is frozen only by submitting its batch'
                        USING ERRCODE = 'restrict_violation';
                END IF;
            ELSIF NEW.released_at IS DISTINCT FROM OLD.released_at THEN
                IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL
                   OR batch_status NOT IN ('draft', 'approved', 'void') THEN
                    RAISE EXCEPTION 'claim_lines: a line is released once, and only while its batch is not submitted'
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_claim_lines_insert BEFORE INSERT ON claim_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_claim_line_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_lines_update BEFORE UPDATE ON claim_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_claim_line_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_lines_no_delete BEFORE DELETE ON claim_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_lines_no_truncate BEFORE TRUNCATE ON claim_lines "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON claim_lines FROM rebate_app")
    op.execute("GRANT UPDATE (frozen_at, released_at) ON claim_lines TO rebate_app")
    enable_tenant_rls("claim_lines")

    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT fk_claim_adjustments_claim_lines "
        "FOREIGN KEY (tenant_id, claim_line_id) REFERENCES claim_lines (tenant_id, id)"
    )
