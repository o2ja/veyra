"""S7.4 adjustments wiring: claim_lines for rebate accruals, claim_adjustments links and the batch tie-out.

DATABASE_SCHEMA.md §3.8; BUSINESS_LOGIC.md §5.5, §7, §9.3. No new table (RLS unchanged), so no new policy.

- ``claim_lines.calculation_result_id`` is NULL exactly for ``source_kind = 'rebate_accrual'`` (orchestrator decision):
  a rebate claim line has an accrual entry, no calculation result.
- ``claim_adjustments``: composite FK ``claim_line_id -> claim_lines``; new ``rebate_accrual_entry_id`` (composite FK)
  for ``rebate_clawback`` (set exactly for that type, amount negative, one clawback per entry); ``included`` exactly
  when ``included_in_batch_id`` is set; approved/included carry approver and time (a rejection records its actor in the audit log only);
  return reversals are negative and, like agreement changes, name their claim line. The S5 one-proposal-per-run index
  gains ``calculation_result_id``: several partial returns of one original in one run are separate proposals.
- Adjustment trigger: ``included`` only from ``approved``, only into a ``draft`` batch of the same manufacturer; an
  included adjustment returns to ``approved`` only when its batch is ``void``. Frozen with a submitted batch.
- Batch tie-out (deferred, at commit): ``total_amount`` = the lines' claim amounts + the batch's included adjustments
  (signed); ``line_count`` counts lines only. A void batch has no adjustments left on it (released to ``approved``);
  its total is not re-derived because the released adjustments are no longer attached.

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261116_0900_adjustments_wiring
Revises: 20261115_0920_claim_lines
"""

from alembic import op

revision = "20261116_0900_adjustments_wiring"
down_revision = "20261115_0920_claim_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE claim_lines ALTER COLUMN calculation_result_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE claim_lines ADD CONSTRAINT ck_claim_lines_result_source "
        "CHECK ((source_kind = 'sales_line') = (calculation_result_id IS NOT NULL))"
    )

    op.execute("ALTER TABLE claim_adjustments ADD COLUMN rebate_accrual_entry_id uuid")
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT fk_claim_adjustments_rebate_accrual_entries "
        "FOREIGN KEY (tenant_id, rebate_accrual_entry_id) REFERENCES rebate_accrual_entries (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT fk_claim_adjustments_claim_lines "
        "FOREIGN KEY (tenant_id, claim_line_id) REFERENCES claim_lines (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT ck_claim_adjustments_clawback CHECK "
        "((adjustment_type = 'rebate_clawback') = (rebate_accrual_entry_id IS NOT NULL) "
        "AND (adjustment_type <> 'rebate_clawback' OR amount < 0))"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT ck_claim_adjustments_included "
        "CHECK ((status = 'included') = (included_in_batch_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT ck_claim_adjustments_approval CHECK "
        "(CASE WHEN status IN ('approved', 'included') THEN approved_by IS NOT NULL AND approved_at IS NOT NULL "
        "WHEN status = 'proposed' THEN approved_by IS NULL AND approved_at IS NULL ELSE true END)"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT ck_claim_adjustments_claim_line CHECK "
        "(adjustment_type NOT IN ('return_reversal', 'agreement_change') OR claim_line_id IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT ck_claim_adjustments_return_negative CHECK "
        "(adjustment_type <> 'return_reversal' OR amount < 0)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_claim_adjustments_clawback ON claim_adjustments (tenant_id, rebate_accrual_entry_id) "
        "WHERE rebate_accrual_entry_id IS NOT NULL"
    )
    op.execute("DROP INDEX uq_claim_adjustments_proposal")
    op.execute(
        "CREATE UNIQUE INDEX uq_claim_adjustments_proposal ON claim_adjustments "
        "(tenant_id, claim_line_id, adjustment_type, run_id, calculation_result_id) WHERE claim_line_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_claim_adjustments_tenant_id_included_in_batch_id ON claim_adjustments "
        "(tenant_id, included_in_batch_id) WHERE included_in_batch_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_claim_adjustment_update() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            batch claim_batches%ROWTYPE;
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
                OR (OLD.status = 'approved' AND NEW.status IN ('included', 'rejected'))
                OR (OLD.status = 'included' AND NEW.status = 'approved')) THEN
                RAISE EXCEPTION 'claim_adjustments: illegal status transition % -> %', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status = 'included' THEN
                SELECT * INTO batch FROM claim_batches
                 WHERE tenant_id = NEW.tenant_id AND id = NEW.included_in_batch_id;
                IF NOT FOUND OR batch.status <> 'draft' OR batch.manufacturer_id <> NEW.manufacturer_id THEN
                    RAISE EXCEPTION 'claim_adjustments: only into a draft batch of the same manufacturer'
                        USING ERRCODE = 'restrict_violation';
                END IF;
            ELSIF OLD.status = 'included' THEN
                SELECT * INTO batch FROM claim_batches
                 WHERE tenant_id = OLD.tenant_id AND id = OLD.included_in_batch_id;
                IF batch.status IS DISTINCT FROM 'void' THEN
                    RAISE EXCEPTION 'claim_adjustments: an included adjustment is released only by voiding its batch'
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_claim_batch_ties_out() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            bid uuid;
            marker text;
            b claim_batches%ROWTYPE;
            n integer;
            total numeric;
            adjustments numeric;
            attached integer;
            unfrozen integer;
            frozen integer;
            unreleased integer;
            released integer;
        BEGIN
            -- fired for batch rows, line rows and adjustment rows; checks the parent batch once per transaction
            bid := (CASE TG_TABLE_NAME WHEN 'claim_batches' THEN to_jsonb(NEW) ->> 'id'
                                       WHEN 'claim_adjustments' THEN to_jsonb(NEW) ->> 'included_in_batch_id'
                                       ELSE to_jsonb(NEW) ->> 'batch_id' END)::uuid;
            IF bid IS NULL THEN
                RETURN NULL;
            END IF;
            marker := 'rebate.claim_check_' || replace(bid::text, '-', '_');
            IF current_setting(marker, true) = 'done' THEN
                RETURN NULL;
            END IF;
            PERFORM set_config(marker, 'done', true);
            SELECT * INTO b FROM claim_batches WHERE tenant_id = NEW.tenant_id AND id = bid;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT count(*), COALESCE(sum(claim_amount), 0),
                   count(*) FILTER (WHERE frozen_at IS NULL), count(*) FILTER (WHERE frozen_at IS NOT NULL),
                   count(*) FILTER (WHERE released_at IS NULL), count(*) FILTER (WHERE released_at IS NOT NULL)
              INTO n, total, unfrozen, frozen, unreleased, released
              FROM claim_lines WHERE tenant_id = b.tenant_id AND batch_id = b.id;
            SELECT count(*), COALESCE(sum(amount), 0) INTO attached, adjustments
              FROM claim_adjustments
             WHERE tenant_id = b.tenant_id AND included_in_batch_id = b.id AND status = 'included';
            IF b.status = 'void' THEN
                IF n <> b.line_count OR attached > 0 THEN
                    RAISE EXCEPTION 'claim_batches: a void batch keeps its lines and releases its adjustments'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF n <> b.line_count OR total + adjustments <> b.total_amount THEN
                RAISE EXCEPTION 'claim_batches: line_count/total_amount (%/%) differ from the lines and adjustments (%/%)',
                    b.line_count, b.total_amount, n, total + adjustments USING ERRCODE = 'check_violation';
            END IF;
            IF (b.status = 'submitted' AND unfrozen > 0) OR (b.status <> 'submitted' AND frozen > 0) THEN
                RAISE EXCEPTION 'claim_batches: lines are frozen exactly when the batch is submitted'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF (b.status = 'void' AND unreleased > 0) OR (b.status <> 'void' AND released > 0) THEN
                RAISE EXCEPTION 'claim_batches: lines are released exactly when the batch is void'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_claim_adjustments_batch_ties_out AFTER INSERT OR UPDATE ON claim_adjustments "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fn_claim_batch_ties_out()"
    )
