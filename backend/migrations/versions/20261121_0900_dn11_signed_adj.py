"""D-N11 (owner decision 2026-11-14): a NEGATIVE adjustment allocation frees room on the remittance line.

``fn_reconciliation_match_within_paid``: per line, the signed sum of ALL confirmed allocations (claim lines and
adjustment items alike) is at most ``amount_paid``. Until now an allocation to an adjustment carried by another batch
counted as ``GREATEST(amount, 0)``; a later payment of the carrying batch is now split as claim line 312.50 plus the
adjustment -50.00 on one line paid 262.50. Everything else in the function is unchanged (same-batch exact amount, per
adjustment cap, serialisation locks). Mirrors ``validate_matches`` in rebate_matching.

Forward-only like the rest of the chain.

Revision ID: 20261121_0900_dn11_signed_adj
Revises: 20261120_0900_after_write_off
"""

from alembic import op

revision = "20261121_0900_dn11_signed_adj"
down_revision = "20261120_0900_after_write_off"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_reconciliation_match_within_paid() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            paid numeric;
            allocated numeric;
            adj_total numeric;
        BEGIN
            IF NEW.status <> 'confirmed' THEN
                RETURN NULL;
            END IF;
            IF NEW.claim_adjustment_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM claim_adjustments a JOIN claim_lines c
                  ON c.tenant_id = a.tenant_id AND c.id = a.claim_line_id
                WHERE a.tenant_id = NEW.tenant_id AND a.id = NEW.claim_adjustment_id
                  AND c.batch_id = a.included_in_batch_id AND a.amount <> NEW.allocated_amount) THEN
                RAISE EXCEPTION 'reconciliation_matches: a same-batch adjustment allocation equals the adjustment amount'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.claim_adjustment_id IS NOT NULL THEN
                PERFORM pg_advisory_xact_lock(hashtextextended('recon-adj:' || NEW.claim_adjustment_id::text, 0));
                SELECT COALESCE(sum(allocated_amount), 0) INTO adj_total FROM reconciliation_matches
                WHERE tenant_id = NEW.tenant_id AND claim_adjustment_id = NEW.claim_adjustment_id
                  AND status = 'confirmed';
                IF abs(adj_total) > (SELECT abs(amount) FROM claim_adjustments
                                     WHERE tenant_id = NEW.tenant_id AND id = NEW.claim_adjustment_id) THEN
                    RAISE EXCEPTION 'reconciliation_matches: confirmed allocations % exceed the adjustment amount',
                        adj_total USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            SELECT amount_paid INTO paid FROM remittance_lines
            WHERE tenant_id = NEW.tenant_id AND id = NEW.remittance_line_id FOR NO KEY UPDATE;
            SELECT COALESCE(sum(allocated_amount), 0) INTO allocated FROM reconciliation_matches
            WHERE tenant_id = NEW.tenant_id AND remittance_line_id = NEW.remittance_line_id
              AND status = 'confirmed';
            IF allocated > paid THEN
                RAISE EXCEPTION 'reconciliation_matches: confirmed allocations % exceed the amount paid %',
                    allocated, paid USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
