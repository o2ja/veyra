"""S9.1 variance_cases, variance_case_events, case_attachments, notifications.

DATABASE_SCHEMA.md §3.10; BUSINESS_LOGIC.md §10.3, §12, §12.1. Tenant tables: composite FKs, RLS ENABLE + FORCE via
the shared helper.

Deviations from the schema doc (also in docs/reports/notes/j1-s9-recovery.md):

- ``case_type`` has no ``deadline_unknown`` value: an unknown deadline is ``deadline_date IS NULL`` (BUSINESS_LOGIC §12
  calls it a flag, not a type); ``deadline_basis`` is then NULL too.
- ``created_by_run_id`` is replaced by ``remittance_document_id``: a case is created by a remittance finalisation,
  not by a calculation run.
- Extra columns: ``revision`` (If-Match), the write-off proposal and approval columns (four-eyes), ``external_reason_code``.
- ``variance_case_events.event_type`` also has ``write_off_proposed`` and ``reclaim_proposed``.

Invariants in the database: state edges of §12.1 (terminal states never change), identity columns fixed, a case row's
revision grows by one per update, the write-off approver differs from the proposer, events and attachments are
append-only, an attachment is a clean-scanned ``attachment`` file. ``claim_adjustments.variance_case_id`` gets its
composite FK now that the table exists.

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261118_0900_recovery_cases
Revises: 20261117_0910_recon_matches
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261118_0900_recovery_cases"
down_revision = "20261117_0910_recon_matches"
branch_labels = None
depends_on = None

MUTABLE = (
    "status",
    "owner_user_id",
    "deadline_date",
    "deadline_basis",
    "recovered_amount",
    "written_off_amount",
    "dispute_assessment",
    "revision",
    "write_off_proposed_by",
    "write_off_proposed_at",
    "write_off_reason",
    "written_off_by",
    "written_off_at",
    "closed_at",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE variance_cases (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            claim_line_id uuid,
            remittance_line_id uuid,
            remittance_document_id uuid,
            case_type text NOT NULL,
            reason_code text NOT NULL,
            external_reason_code text,
            claimed_amount numeric(18, 4),
            paid_amount numeric(18, 4),
            variance_amount numeric(18, 4) NOT NULL,
            deadline_date date,
            deadline_basis text,
            owner_user_id uuid,
            status text NOT NULL DEFAULT 'open',
            recovered_amount numeric(18, 4) NOT NULL DEFAULT 0,
            written_off_amount numeric(18, 4) NOT NULL DEFAULT 0,
            dispute_assessment jsonb,
            revision integer NOT NULL DEFAULT 1,
            write_off_proposed_by uuid,
            write_off_proposed_at timestamptz,
            write_off_reason text,
            written_off_by uuid,
            written_off_at timestamptz,
            created_by uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            closed_at timestamptz,
            CONSTRAINT pk_variance_cases PRIMARY KEY (id),
            CONSTRAINT uq_variance_cases_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_variance_cases_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_variance_cases_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_variance_cases_claim_lines FOREIGN KEY (tenant_id, claim_line_id)
                REFERENCES claim_lines (tenant_id, id),
            CONSTRAINT fk_variance_cases_remittance_lines FOREIGN KEY (tenant_id, remittance_line_id)
                REFERENCES remittance_lines (tenant_id, id),
            CONSTRAINT fk_variance_cases_remittance_documents FOREIGN KEY (tenant_id, remittance_document_id)
                REFERENCES remittance_documents (tenant_id, id),
            CONSTRAINT fk_variance_cases_reason_codes FOREIGN KEY (reason_code) REFERENCES reason_codes (code),
            CONSTRAINT fk_variance_cases_users_owner FOREIGN KEY (owner_user_id) REFERENCES users (id),
            CONSTRAINT fk_variance_cases_users_proposer FOREIGN KEY (write_off_proposed_by) REFERENCES users (id),
            CONSTRAINT fk_variance_cases_users_written_off FOREIGN KEY (written_off_by) REFERENCES users (id),
            CONSTRAINT fk_variance_cases_users_created FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT ck_variance_cases_type CHECK (
                case_type IN ('short_paid', 'rejected', 'unpaid', 'overpaid', 'unmatched_payment', 'source_changed')),
            CONSTRAINT ck_variance_cases_status CHECK (
                status IN ('open', 'to_reclaim', 'submitted', 'recovered', 'written_off', 'closed_no_action')),
            CONSTRAINT ck_variance_cases_source CHECK (num_nonnulls(claim_line_id, remittance_line_id) >= 1),
            CONSTRAINT ck_variance_cases_variance CHECK (variance_amount > 0),
            CONSTRAINT ck_variance_cases_amounts CHECK (recovered_amount >= 0 AND written_off_amount >= 0),
            CONSTRAINT ck_variance_cases_deadline CHECK (
                (deadline_date IS NULL) = (deadline_basis IS NULL)
                AND (deadline_basis IS NULL OR deadline_basis IN ('remittance_date', 'submission_date'))),
            CONSTRAINT ck_variance_cases_revision CHECK (revision >= 1),
            CONSTRAINT ck_variance_cases_closed CHECK (
                (status IN ('recovered', 'written_off', 'closed_no_action')) = (closed_at IS NOT NULL)),
            CONSTRAINT ck_variance_cases_recovered CHECK (
                status <> 'recovered' OR recovered_amount >= variance_amount),
            CONSTRAINT ck_variance_cases_write_off CHECK (
                (status = 'written_off') = (written_off_by IS NOT NULL AND written_off_at IS NOT NULL)
                AND (written_off_amount = 0 OR status = 'written_off')
                AND (write_off_proposed_by IS NULL) = (write_off_proposed_at IS NULL)),
            CONSTRAINT ck_variance_cases_written_off_amount CHECK (
                status <> 'written_off' OR written_off_amount = variance_amount - recovered_amount),
            CONSTRAINT ck_variance_cases_four_eyes CHECK (
                written_off_by IS NULL OR write_off_proposed_by IS NULL OR written_off_by <> write_off_proposed_by),
            CONSTRAINT ck_variance_cases_reason_len CHECK (char_length(write_off_reason) BETWEEN 1 AND 1000)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_variance_cases_live_claim_line ON variance_cases (tenant_id, claim_line_id, case_type) "
        "WHERE claim_line_id IS NOT NULL AND status NOT IN ('recovered', 'written_off', 'closed_no_action')"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_variance_cases_live_remittance_line ON variance_cases "
        "(tenant_id, remittance_line_id, case_type) WHERE claim_line_id IS NULL "
        "AND status NOT IN ('recovered', 'written_off', 'closed_no_action')"
    )
    op.execute(
        "CREATE INDEX ix_variance_cases_tenant_id_status_deadline_date ON variance_cases "
        "(tenant_id, status, deadline_date)"
    )
    op.execute(
        "CREATE INDEX ix_variance_cases_tenant_id_manufacturer_id ON variance_cases (tenant_id, manufacturer_id, id)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_variance_case_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'open' OR NEW.revision <> 1 OR NEW.recovered_amount <> 0 OR NEW.written_off_amount <> 0
               OR NEW.write_off_proposed_by IS NOT NULL OR NEW.written_off_by IS NOT NULL THEN
                RAISE EXCEPTION 'variance_cases: a new case is open, untouched and revision 1'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    mutable = " - ".join(f"'{c}'" for c in MUTABLE)
    op.execute(
        f"""
        CREATE FUNCTION fn_variance_case_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('recovered', 'written_off', 'closed_no_action') THEN
                RAISE EXCEPTION 'variance_cases: a % case never changes', OLD.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - {mutable}) IS DISTINCT FROM (to_jsonb(OLD) - {mutable}) THEN
                RAISE EXCEPTION 'variance_cases: identity, amounts and links are fixed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'variance_cases: every change bumps the revision by one'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.recovered_amount < OLD.recovered_amount THEN
                RAISE EXCEPTION 'variance_cases: recovered amount never decreases'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status <> OLD.status AND NOT (
                (OLD.status = 'open' AND NEW.status IN ('to_reclaim', 'closed_no_action', 'written_off'))
                OR (OLD.status = 'to_reclaim' AND NEW.status IN ('submitted', 'written_off'))
                OR (OLD.status = 'submitted' AND NEW.status IN ('recovered', 'to_reclaim', 'written_off'))) THEN
                RAISE EXCEPTION 'variance_cases: % -> % is not a case transition', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_variance_cases_insert BEFORE INSERT ON variance_cases "
        "FOR EACH ROW EXECUTE FUNCTION fn_variance_case_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_variance_cases_update BEFORE UPDATE ON variance_cases "
        "FOR EACH ROW EXECUTE FUNCTION fn_variance_case_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_variance_cases_no_delete BEFORE DELETE ON variance_cases "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_variance_cases_no_truncate BEFORE TRUNCATE ON variance_cases "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE DELETE, TRUNCATE ON variance_cases FROM rebate_app")
    enable_tenant_rls("variance_cases")

    op.execute(
        """
        CREATE TABLE variance_case_events (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            case_id uuid NOT NULL,
            event_type text NOT NULL,
            from_status text,
            to_status text,
            note text,
            payload jsonb,
            actor_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_variance_case_events PRIMARY KEY (id),
            CONSTRAINT uq_variance_case_events_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_variance_case_events_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_variance_case_events_variance_cases FOREIGN KEY (tenant_id, case_id)
                REFERENCES variance_cases (tenant_id, id),
            CONSTRAINT fk_variance_case_events_users FOREIGN KEY (actor_user_id) REFERENCES users (id),
            CONSTRAINT ck_variance_case_events_type CHECK (event_type IN (
                'created', 'status_changed', 'note', 'assigned', 'attachment', 'amount_recovered',
                'assessment_updated', 'write_off_proposed', 'reclaim_proposed')),
            CONSTRAINT ck_variance_case_events_status CHECK (
                (event_type = 'status_changed') = (to_status IS NOT NULL)),
            CONSTRAINT ck_variance_case_events_note CHECK (char_length(note) BETWEEN 1 AND 2000)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_variance_case_events_tenant_id_case_id ON variance_case_events "
        "(tenant_id, case_id, created_at, id)"
    )
    for stmt in (
        "CREATE TRIGGER trg_variance_case_events_append_only BEFORE UPDATE OR DELETE ON variance_case_events "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()",
        "CREATE TRIGGER trg_variance_case_events_no_truncate BEFORE TRUNCATE ON variance_case_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()",
        "REVOKE UPDATE, DELETE, TRUNCATE ON variance_case_events FROM rebate_app",
    ):
        op.execute(stmt)
    enable_tenant_rls("variance_case_events")

    op.execute(
        """
        CREATE TABLE case_attachments (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            case_id uuid NOT NULL,
            file_id uuid NOT NULL,
            added_by uuid NOT NULL,
            added_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_case_attachments PRIMARY KEY (id),
            CONSTRAINT uq_case_attachments_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_case_attachments_case_file UNIQUE (tenant_id, case_id, file_id),
            CONSTRAINT fk_case_attachments_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_case_attachments_variance_cases FOREIGN KEY (tenant_id, case_id)
                REFERENCES variance_cases (tenant_id, id),
            CONSTRAINT fk_case_attachments_file_objects FOREIGN KEY (tenant_id, file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_case_attachments_users FOREIGN KEY (added_by) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_case_attachment_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            f_kind text;
            f_scan text;
        BEGIN
            SELECT kind, scan_status INTO f_kind, f_scan FROM file_objects
            WHERE tenant_id = NEW.tenant_id AND id = NEW.file_id;
            IF f_kind IS DISTINCT FROM 'attachment' OR f_scan IS DISTINCT FROM 'clean' THEN
                RAISE EXCEPTION 'case_attachments: only a clean-scanned attachment file can be attached'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for stmt in (
        "CREATE TRIGGER trg_case_attachments_insert BEFORE INSERT ON case_attachments "
        "FOR EACH ROW EXECUTE FUNCTION fn_case_attachment_insert()",
        "CREATE TRIGGER trg_case_attachments_append_only BEFORE UPDATE OR DELETE ON case_attachments "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()",
        "CREATE TRIGGER trg_case_attachments_no_truncate BEFORE TRUNCATE ON case_attachments "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()",
        "REVOKE UPDATE, DELETE, TRUNCATE ON case_attachments FROM rebate_app",
    ):
        op.execute(stmt)
    enable_tenant_rls("case_attachments")

    op.execute(
        """
        CREATE TABLE notifications (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            kind text NOT NULL,
            entity_type text,
            entity_id uuid,
            title text NOT NULL,
            body text,
            due_date date,
            dedupe_key text NOT NULL,
            read_at timestamptz,
            emailed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_notifications PRIMARY KEY (id),
            CONSTRAINT uq_notifications_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_notifications_dedupe UNIQUE (tenant_id, user_id, dedupe_key),
            CONSTRAINT fk_notifications_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_notifications_users FOREIGN KEY (user_id) REFERENCES users (id),
            CONSTRAINT ck_notifications_kind CHECK (kind IN ('deadline_reminder')),
            CONSTRAINT ck_notifications_title CHECK (char_length(title) BETWEEN 1 AND 300)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_notifications_tenant_id_user_id ON notifications (tenant_id, user_id, created_at DESC, id)"
    )
    for stmt in (
        "CREATE FUNCTION fn_notification_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF (to_jsonb(NEW) - 'read_at' - 'emailed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'read_at' - 'emailed_at') "
        "OR (OLD.read_at IS NOT NULL AND NEW.read_at IS DISTINCT FROM OLD.read_at) "
        "OR (OLD.emailed_at IS NOT NULL AND NEW.emailed_at IS DISTINCT FROM OLD.emailed_at) THEN "
        "RAISE EXCEPTION 'notifications: only read_at and emailed_at are set, once' "
        "USING ERRCODE = 'restrict_violation'; END IF; RETURN NEW; END $$",
        "CREATE TRIGGER trg_notifications_update BEFORE UPDATE ON notifications "
        "FOR EACH ROW EXECUTE FUNCTION fn_notification_update()",
        "CREATE TRIGGER trg_notifications_no_delete BEFORE DELETE ON notifications "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()",
        "CREATE TRIGGER trg_notifications_no_truncate BEFORE TRUNCATE ON notifications "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()",
        "REVOKE UPDATE, DELETE, TRUNCATE ON notifications FROM rebate_app",
        "GRANT UPDATE (read_at, emailed_at) ON notifications TO rebate_app",
    ):
        op.execute(stmt)
    enable_tenant_rls("notifications")

    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT fk_claim_adjustments_variance_cases "
        "FOREIGN KEY (tenant_id, variance_case_id) REFERENCES variance_cases (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT ck_claim_adjustments_reclaim CHECK "
        "((adjustment_type = 'recovery_reclaim') = (variance_case_id IS NOT NULL) "
        "AND (adjustment_type <> 'recovery_reclaim' OR amount > 0))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_claim_adjustments_live_reclaim ON claim_adjustments (tenant_id, variance_case_id) "
        "WHERE adjustment_type = 'recovery_reclaim' AND status IN ('proposed', 'approved')"
    )
