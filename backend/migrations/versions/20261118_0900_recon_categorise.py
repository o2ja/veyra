"""S8 per-line categorisation: a person's decision that an unmatched remittance line is an unmatched payment.

BUSINESS_LOGIC.md §10.3 makes finalisation require "all unmatched lines explicitly categorised". ``remittance_lines``
stays append-only evidence except for this one decision:

- ``category`` (only ``unmatched_payment`` for now), ``categorised_by`` and ``categorised_at`` are set together, once.
- The decision is possible only while the document is ``accepted`` and the line has no confirmed match; nothing else
  on the line ever changes, and a line is never deleted.
- ``rebate_app`` may update exactly these three columns.

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261118_0900_recon_categorise
Revises: 20261117_0910_recon_matches
"""

from alembic import op

revision = "20261118_0900_recon_categorise"
down_revision = "20261117_0910_recon_matches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE remittance_lines
            ADD COLUMN category text,
            ADD COLUMN categorised_by uuid,
            ADD COLUMN categorised_at timestamptz,
            ADD CONSTRAINT fk_remittance_lines_users_categorised FOREIGN KEY (categorised_by) REFERENCES users (id),
            ADD CONSTRAINT ck_remittance_lines_category CHECK (category IS NULL OR category = 'unmatched_payment'),
            ADD CONSTRAINT ck_remittance_lines_categorised CHECK (
                (category IS NULL) = (categorised_by IS NULL) AND (category IS NULL) = (categorised_at IS NULL))
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_remittance_line_categorise() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            doc_status text;
        BEGIN
            SELECT status INTO doc_status FROM remittance_documents
            WHERE tenant_id = OLD.tenant_id AND id = OLD.remittance_document_id;
            IF OLD.category IS NOT NULL OR NEW.category IS NULL OR doc_status IS DISTINCT FROM 'accepted'
               OR (to_jsonb(NEW) - 'category' - 'categorised_by' - 'categorised_at') IS DISTINCT FROM
                  (to_jsonb(OLD) - 'category' - 'categorised_by' - 'categorised_at') THEN
                RAISE EXCEPTION 'remittance_lines: only a one-time categorisation on an accepted remittance is allowed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF EXISTS (
                SELECT 1 FROM reconciliation_matches
                WHERE tenant_id = OLD.tenant_id AND remittance_line_id = OLD.id AND status = 'confirmed') THEN
                RAISE EXCEPTION 'remittance_lines: a line with a confirmed match cannot be categorised'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER trg_remittance_lines_no_mutation ON remittance_lines")
    op.execute(
        "CREATE TRIGGER trg_remittance_lines_no_delete BEFORE DELETE ON remittance_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_remittance_lines_categorise BEFORE UPDATE ON remittance_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_remittance_line_categorise()"
    )
    op.execute("GRANT UPDATE (category, categorised_by, categorised_at) ON remittance_lines TO rebate_app")
