"""D-N11 (owner decision 2026-11-14): a NEGATIVE adjustment allocation frees room on its carrying batch's payment.

``fn_reconciliation_match_within_paid``: an allocation to an adjustment carried by another batch still counts as
``GREATEST(amount, 0)``, EXCEPT a negative one on a remittance line that also has a confirmed allocation to a claim
line of the adjustment's carrying batch (``included_in_batch_id``): there it counts signed, so a payment of 262.50 on
the carrying line splits as claim line 312.50 plus the adjustment -50.00. On any other line a negative adjustment
allocation never frees room for other claim lines. New: the allocation's sign equals the adjustment's sign (today only
the absolute sum was capped, so raw rows -100 and +50 on a -50.00 adjustment passed). Everything else in the function is
unchanged (same-batch exact amount, per-adjustment cap, serialisation locks). Mirrors ``validate_matches``.

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
            IF NEW.claim_adjustment_id IS NOT NULL AND sign(NEW.allocated_amount) <> (
                SELECT sign(amount) FROM claim_adjustments
                WHERE tenant_id = NEW.tenant_id AND id = NEW.claim_adjustment_id) THEN
                RAISE EXCEPTION 'reconciliation_matches: an adjustment allocation has the adjustment''s sign'
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
            SELECT COALESCE(sum(CASE WHEN m.claim_line_id IS NOT NULL OR info.same_batch
                                       OR (m.allocated_amount < 0 AND info.on_carrying_line)
                                     THEN m.allocated_amount ELSE GREATEST(m.allocated_amount, 0) END), 0)
            INTO allocated
            FROM reconciliation_matches m
            LEFT JOIN LATERAL (
                SELECT EXISTS (
                    SELECT 1 FROM claim_adjustments a JOIN claim_lines c
                      ON c.tenant_id = a.tenant_id AND c.id = a.claim_line_id
                    WHERE a.tenant_id = m.tenant_id AND a.id = m.claim_adjustment_id
                      AND c.batch_id = a.included_in_batch_id) AS same_batch,
                    EXISTS (
                    SELECT 1 FROM claim_adjustments a
                    JOIN claim_lines carrier ON carrier.tenant_id = a.tenant_id
                      AND carrier.batch_id = a.included_in_batch_id
                    JOIN reconciliation_matches p ON p.tenant_id = carrier.tenant_id
                      AND p.claim_line_id = carrier.id AND p.remittance_line_id = m.remittance_line_id
                      AND p.status = 'confirmed'
                    WHERE a.tenant_id = m.tenant_id AND a.id = m.claim_adjustment_id) AS on_carrying_line
            ) info ON true
            WHERE m.tenant_id = NEW.tenant_id AND m.remittance_line_id = NEW.remittance_line_id
              AND m.status = 'confirmed';
            IF allocated > paid THEN
                RAISE EXCEPTION 'reconciliation_matches: confirmed allocations % exceed the amount paid %',
                    allocated, paid USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
