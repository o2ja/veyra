"""S8 guards on reconciliation_matches: the payment invariant ignores cross-batch adjustment items, and a categorised
line cannot be matched.

- ``fn_reconciliation_match_within_paid``: serialised per remittance line (``FOR NO KEY UPDATE`` on the line) and per
  adjustment item (advisory lock); the confirmed allocations of one adjustment item sum to at most its amount in
  absolute value; per line, the
  confirmed allocations to claim lines plus those to an adjustment carried by its original line's own batch (which must
  equal the adjustment's amount exactly: ``allocate()`` writes face value) plus ``GREATEST(amount, 0)`` of those to an
  adjustment carried by ANOTHER batch (owner decision 2026-11-13; interim, pending D-N11) sum to at most ``amount_paid``:
  a negative cross-batch allocation never frees room for claim lines, a positive one consumes room (mirrors
  ``validate_matches`` in rebate_matching).
- ``fn_reconciliation_match_insert`` / ``fn_reconciliation_match_change``: a ``confirmed`` match on a line with a
  ``category`` is refused (categorisation is final).

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261118_0910_recon_guards
Revises: 20261118_0900_recon_categorise
"""

from alembic import op

revision = "20261118_0910_recon_guards"
down_revision = "20261118_0900_recon_categorise"
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
                PERFORM pg_advisory_xact_lock(hashtext(NEW.claim_adjustment_id::text));
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
            SELECT COALESCE(sum(CASE WHEN m.claim_line_id IS NOT NULL OR same_batch.ok THEN m.allocated_amount
                                     ELSE GREATEST(m.allocated_amount, 0) END), 0) INTO allocated
            FROM reconciliation_matches m
            LEFT JOIN LATERAL (
                SELECT EXISTS (
                    SELECT 1 FROM claim_adjustments a JOIN claim_lines c
                      ON c.tenant_id = a.tenant_id AND c.id = a.claim_line_id
                    WHERE a.tenant_id = m.tenant_id AND a.id = m.claim_adjustment_id
                      AND c.batch_id = a.included_in_batch_id) AS ok) same_batch ON true
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
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_reconciliation_match_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            doc_status text;
            doc_manufacturer uuid;
            target_manufacturer uuid;
        BEGIN
            IF NEW.status NOT IN ('suggested', 'confirmed') OR NEW.revision <> 1 THEN
                RAISE EXCEPTION 'reconciliation_matches: a new match is suggested or confirmed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            SELECT d.status, d.manufacturer_id INTO doc_status, doc_manufacturer
            FROM remittance_lines l JOIN remittance_documents d
              ON d.tenant_id = l.tenant_id AND d.id = l.remittance_document_id
            WHERE l.tenant_id = NEW.tenant_id AND l.id = NEW.remittance_line_id;
            IF doc_status IS DISTINCT FROM 'accepted' THEN
                RAISE EXCEPTION 'reconciliation_matches: matches can be added only to an accepted remittance'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status = 'confirmed' AND EXISTS (
                SELECT 1 FROM remittance_lines
                WHERE tenant_id = NEW.tenant_id AND id = NEW.remittance_line_id AND category IS NOT NULL) THEN
                RAISE EXCEPTION 'reconciliation_matches: a categorised line cannot be matched'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.claim_line_id IS NOT NULL THEN
                SELECT b.manufacturer_id INTO target_manufacturer
                FROM claim_lines c JOIN claim_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
                WHERE c.tenant_id = NEW.tenant_id AND c.id = NEW.claim_line_id;
            ELSE
                SELECT manufacturer_id INTO target_manufacturer
                FROM claim_adjustments WHERE tenant_id = NEW.tenant_id AND id = NEW.claim_adjustment_id;
            END IF;
            IF target_manufacturer IS DISTINCT FROM doc_manufacturer THEN
                RAISE EXCEPTION 'reconciliation_matches: a match never crosses manufacturers'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_reconciliation_match_change() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            doc_status text;
        BEGIN
            SELECT d.status INTO doc_status
            FROM remittance_lines l JOIN remittance_documents d
              ON d.tenant_id = l.tenant_id AND d.id = l.remittance_document_id
            WHERE l.tenant_id = OLD.tenant_id AND l.id = OLD.remittance_line_id;
            IF OLD.status <> 'suggested' OR doc_status IS DISTINCT FROM 'accepted' THEN
                RAISE EXCEPTION 'reconciliation_matches: a decided match (or one of a finalised remittance) never changes'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            IF NEW.status = 'confirmed' AND EXISTS (
                SELECT 1 FROM remittance_lines
                WHERE tenant_id = OLD.tenant_id AND id = OLD.remittance_line_id AND category IS NOT NULL) THEN
                RAISE EXCEPTION 'reconciliation_matches: a categorised line cannot be matched'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'revision' - 'confirmed_by' - 'confirmed_at' - 'rejected_by'
                    - 'rejected_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'revision' - 'confirmed_by' - 'confirmed_at' - 'rejected_by'
                    - 'rejected_at') OR NEW.status = 'suggested' OR NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'reconciliation_matches: a suggestion is only confirmed or rejected'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
