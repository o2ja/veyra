"""S8.3b reconciliation_matches: one row per allocation of a remittance line to a claim line or an adjustment.

DATABASE_SCHEMA.md §3.9; BUSINESS_LOGIC.md §10.2. Tenant table: composite FKs, RLS ENABLE + FORCE via the shared helper.

- ``match_id`` (addition to the schema doc) groups the allocation rows of one logical match: a user confirms or
  rejects the whole match (e.g. a claim plus its return adjustment, or a lump payment over several claims).
- Rows are inserted ``suggested`` or ``confirmed`` and only while the remittance is ``accepted`` (never after
  finalisation), against a claim line / adjustment of the remittance's manufacturer.
- A ``suggested`` row may become ``confirmed`` or ``rejected`` (once; the actor and time are set with it) and may be
  deleted (a re-run replaces suggestions). ``confirmed`` and ``rejected`` rows never change or go away.
- Invariant in the database: per remittance line, the confirmed allocations sum to at most ``amount_paid``.

- ``ai_invocations.purpose`` also allows ``remittance_pass`` and ``remittance_repair`` (the PDF remittance extraction
  calls the same AI gateway; the CHECK is the only change to the extraction tables).

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261117_0910_recon_matches
Revises: 20261117_0900_remittances
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261117_0910_recon_matches"
down_revision = "20261117_0900_remittances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reconciliation_matches (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            match_id uuid NOT NULL,
            remittance_line_id uuid NOT NULL,
            claim_line_id uuid,
            claim_adjustment_id uuid,
            allocated_amount numeric(18, 4) NOT NULL,
            method text NOT NULL,
            score numeric(5, 4),
            evidence jsonb NOT NULL,
            status text NOT NULL,
            revision integer NOT NULL DEFAULT 1,
            matched_by_run_id uuid,
            confirmed_by uuid,
            confirmed_at timestamptz,
            rejected_by uuid,
            rejected_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_reconciliation_matches PRIMARY KEY (id),
            CONSTRAINT uq_reconciliation_matches_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_reconciliation_matches_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_reconciliation_matches_remittance_lines FOREIGN KEY (tenant_id, remittance_line_id)
                REFERENCES remittance_lines (tenant_id, id),
            CONSTRAINT fk_reconciliation_matches_claim_lines FOREIGN KEY (tenant_id, claim_line_id)
                REFERENCES claim_lines (tenant_id, id),
            CONSTRAINT fk_reconciliation_matches_claim_adjustments FOREIGN KEY (tenant_id, claim_adjustment_id)
                REFERENCES claim_adjustments (tenant_id, id),
            CONSTRAINT fk_reconciliation_matches_users_confirmed FOREIGN KEY (confirmed_by) REFERENCES users (id),
            CONSTRAINT fk_reconciliation_matches_users_rejected FOREIGN KEY (rejected_by) REFERENCES users (id),
            CONSTRAINT ck_reconciliation_matches_target CHECK (num_nonnulls(claim_line_id, claim_adjustment_id) = 1),
            CONSTRAINT ck_reconciliation_matches_method CHECK (
                method IN ('exact_reference', 'invoice_line', 'composite', 'fuzzy', 'subset_sum', 'manual')),
            CONSTRAINT ck_reconciliation_matches_status CHECK (status IN ('suggested', 'confirmed', 'rejected')),
            CONSTRAINT ck_reconciliation_matches_score CHECK (score BETWEEN 0 AND 1),
            CONSTRAINT ck_reconciliation_matches_decision CHECK (
                (status = 'confirmed') = (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)
                AND (status = 'rejected') = (rejected_by IS NOT NULL AND rejected_at IS NOT NULL)),
            CONSTRAINT ck_reconciliation_matches_revision CHECK (revision >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_matches_tenant_id_claim_line_id ON reconciliation_matches "
        "(tenant_id, claim_line_id)"
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_matches_tenant_id_remittance_line_id ON reconciliation_matches "
        "(tenant_id, remittance_line_id)"
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_matches_tenant_id_match_id ON reconciliation_matches (tenant_id, match_id)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_reconciliation_match_insert() RETURNS trigger LANGUAGE plpgsql AS $$
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
        CREATE FUNCTION fn_reconciliation_match_change() RETURNS trigger LANGUAGE plpgsql AS $$
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
    op.execute(
        """
        CREATE FUNCTION fn_reconciliation_match_within_paid() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            paid numeric;
            allocated numeric;
        BEGIN
            IF NEW.status <> 'confirmed' THEN
                RETURN NULL;
            END IF;
            SELECT amount_paid INTO paid FROM remittance_lines
            WHERE tenant_id = NEW.tenant_id AND id = NEW.remittance_line_id;
            SELECT COALESCE(sum(allocated_amount), 0) INTO allocated FROM reconciliation_matches
            WHERE tenant_id = NEW.tenant_id AND remittance_line_id = NEW.remittance_line_id AND status = 'confirmed';
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
        "CREATE TRIGGER trg_reconciliation_matches_insert BEFORE INSERT ON reconciliation_matches "
        "FOR EACH ROW EXECUTE FUNCTION fn_reconciliation_match_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_reconciliation_matches_change BEFORE UPDATE OR DELETE ON reconciliation_matches "
        "FOR EACH ROW EXECUTE FUNCTION fn_reconciliation_match_change()"
    )
    op.execute(
        "CREATE TRIGGER trg_reconciliation_matches_within_paid AFTER INSERT OR UPDATE ON reconciliation_matches "
        "FOR EACH ROW EXECUTE FUNCTION fn_reconciliation_match_within_paid()"
    )
    op.execute(
        "CREATE TRIGGER trg_reconciliation_matches_no_truncate BEFORE TRUNCATE ON reconciliation_matches "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, TRUNCATE ON reconciliation_matches FROM rebate_app")
    op.execute(
        "GRANT UPDATE (status, revision, confirmed_by, confirmed_at, rejected_by, rejected_at) "
        "ON reconciliation_matches TO rebate_app"
    )
    enable_tenant_rls("reconciliation_matches")

    op.execute("ALTER TABLE ai_invocations DROP CONSTRAINT ck_ai_invocations_purpose")
    op.execute(
        "ALTER TABLE ai_invocations ADD CONSTRAINT ck_ai_invocations_purpose CHECK (purpose IN "
        "('agreement_pass_a', 'agreement_pass_b', 'agreement_repair', 'mapping', 'mapping_repair', "
        "'remittance_pass', 'remittance_repair'))"
    )
