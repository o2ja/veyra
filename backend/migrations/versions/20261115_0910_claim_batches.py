"""S7.1 claim_batches: one manufacturer's claim batch, draft -> approved -> submitted, draft|approved -> void.

DATABASE_SCHEMA.md §3.8, §7, §8. Tenant table: composite FKs, RLS ENABLE + FORCE via the shared helper.

- Rows are inserted ``draft`` only. The app role may update just the workflow columns; a trigger pins everything
  else (manufacturer, number, period, line count, total) and validates transitions and the columns each one sets.
  A ``void`` batch never changes; a ``submitted`` batch changes only ``external_reference``.
- Never deleted (privileges revoked + trigger).
- Deferred constraint trigger (at commit): ``line_count`` and ``total_amount`` equal the batch's lines, a submitted
  batch has every line frozen, a voided one has every line released, and a draft/approved one has neither
  (totals tie out in the database, not only in code).
- ``submitted_at`` is the submission *date* the user records (FR-CB-04), not a timestamp: the moment is on the
  lines' ``frozen_at`` and in the audit log.
- ``claim_adjustments.included_in_batch_id`` gets its composite FK here (placeholder since S5).

Forward-only like the rest of the chain (no downgrade functions).

Revision ID: 20261115_0910_claim_batches
Revises: 20261115_0900_claim_templates
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261115_0910_claim_batches"
down_revision = "20261115_0900_claim_templates"
branch_labels = None
depends_on = None

WORKFLOW = (
    "status",
    "export_template_id",
    "exported_file_id",
    "submitted_at",
    "submitted_by",
    "external_reference",
    "approved_by",
    "approved_at",
    "void_reason",
    "voided_at",
)
# columns a transition may set (all other columns must stay as they were)
APPROVE = ("status", "approved_by", "approved_at")
SUBMIT = ("status", "submitted_at", "submitted_by", "external_reference")
VOID = ("status", "void_reason", "voided_at")
EXPORT = ("export_template_id", "exported_file_id")


def _minus(columns: tuple[str, ...]) -> str:
    return " - ".join(f"'{c}'" for c in columns)


def _arr(columns: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{c}'" for c in columns) + "]"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE claim_batches (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            manufacturer_id uuid NOT NULL,
            batch_number text NOT NULL,
            status text NOT NULL DEFAULT 'draft',
            period_from date,
            period_to date,
            line_count integer NOT NULL,
            total_amount numeric(18, 4) NOT NULL,
            export_template_id uuid,
            exported_file_id uuid,
            submitted_at date,
            submitted_by uuid,
            external_reference text,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            approved_by uuid,
            approved_at timestamptz,
            void_reason text,
            voided_at timestamptz,
            CONSTRAINT pk_claim_batches PRIMARY KEY (id),
            CONSTRAINT uq_claim_batches_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_claim_batches_batch_number UNIQUE (tenant_id, batch_number),
            CONSTRAINT fk_claim_batches_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_claim_batches_manufacturers FOREIGN KEY (tenant_id, manufacturer_id)
                REFERENCES manufacturers (tenant_id, id),
            CONSTRAINT fk_claim_batches_claim_export_templates FOREIGN KEY (tenant_id, export_template_id)
                REFERENCES claim_export_templates (tenant_id, id),
            CONSTRAINT fk_claim_batches_file_objects FOREIGN KEY (tenant_id, exported_file_id)
                REFERENCES file_objects (tenant_id, id),
            CONSTRAINT fk_claim_batches_users_created FOREIGN KEY (created_by) REFERENCES users (id),
            CONSTRAINT fk_claim_batches_users_approved FOREIGN KEY (approved_by) REFERENCES users (id),
            CONSTRAINT fk_claim_batches_users_submitted FOREIGN KEY (submitted_by) REFERENCES users (id),
            CONSTRAINT ck_claim_batches_status CHECK (status IN ('draft', 'approved', 'submitted', 'void')),
            CONSTRAINT ck_claim_batches_period
                CHECK (period_from IS NULL OR period_to IS NULL OR period_to >= period_from),
            CONSTRAINT ck_claim_batches_line_count CHECK (line_count >= 1),
            CONSTRAINT ck_claim_batches_approved CHECK (
                status NOT IN ('approved', 'submitted') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
            CONSTRAINT ck_claim_batches_submitted CHECK (
                (status = 'submitted') = (submitted_at IS NOT NULL AND submitted_by IS NOT NULL
                                          AND external_reference IS NOT NULL)),
            CONSTRAINT ck_claim_batches_external_reference
                CHECK (external_reference IS NULL OR char_length(external_reference) BETWEEN 1 AND 200),
            CONSTRAINT ck_claim_batches_void CHECK (
                (status = 'void') = (void_reason IS NOT NULL AND voided_at IS NOT NULL)),
            CONSTRAINT ck_claim_batches_void_reason
                CHECK (void_reason IS NULL OR char_length(void_reason) BETWEEN 1 AND 1000)
        )
        """
    )
    op.execute("CREATE INDEX ix_claim_batches_tenant_id_status_id ON claim_batches (tenant_id, status, id)")
    op.execute(
        "CREATE INDEX ix_claim_batches_tenant_id_manufacturer_id ON claim_batches "
        "(tenant_id, manufacturer_id, id)"
    )
    op.execute(
        """
        CREATE FUNCTION fn_claim_batch_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'draft' THEN
                RAISE EXCEPTION 'claim_batches: a batch is created as draft' USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION fn_claim_batch_update() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            allowed text[];
        BEGIN
            IF (to_jsonb(NEW) - {_minus(WORKFLOW)}) IS DISTINCT FROM (to_jsonb(OLD) - {_minus(WORKFLOW)}) THEN
                RAISE EXCEPTION 'claim_batches: manufacturer, number, period, count, total and creator are immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status = 'void' THEN
                RAISE EXCEPTION 'claim_batches: a void batch never changes' USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status = 'submitted' THEN
                IF (to_jsonb(NEW) - 'external_reference') IS DISTINCT FROM (to_jsonb(OLD) - 'external_reference') THEN
                    RAISE EXCEPTION 'claim_batches: a submitted batch changes only its external reference'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status = OLD.status THEN
                allowed := {_arr(EXPORT)};
            ELSIF OLD.status = 'draft' AND NEW.status = 'approved' THEN
                allowed := {_arr(APPROVE + EXPORT)};
            ELSIF OLD.status = 'approved' AND NEW.status = 'submitted' THEN
                allowed := {_arr(SUBMIT + EXPORT)};
            ELSIF OLD.status IN ('draft', 'approved') AND NEW.status = 'void' THEN
                allowed := {_arr(VOID + EXPORT)};
            ELSE
                RAISE EXCEPTION 'claim_batches: illegal status transition % -> %', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
                RAISE EXCEPTION 'claim_batches: this transition may not change those columns'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_claim_batch_ties_out() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            n integer;
            total numeric;
            unfrozen integer;
            frozen integer;
            unreleased integer;
            released integer;
        BEGIN
            SELECT count(*), COALESCE(sum(claim_amount), 0),
                   count(*) FILTER (WHERE frozen_at IS NULL), count(*) FILTER (WHERE frozen_at IS NOT NULL),
                   count(*) FILTER (WHERE released_at IS NULL), count(*) FILTER (WHERE released_at IS NOT NULL)
              INTO n, total, unfrozen, frozen, unreleased, released
              FROM claim_lines WHERE tenant_id = NEW.tenant_id AND batch_id = NEW.id;
            IF n <> NEW.line_count OR total <> NEW.total_amount THEN
                RAISE EXCEPTION 'claim_batches: line_count/total_amount (%/%) differ from the lines (%/%)',
                    NEW.line_count, NEW.total_amount, n, total USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.status = 'submitted' AND unfrozen > 0) OR (NEW.status <> 'submitted' AND frozen > 0) THEN
                RAISE EXCEPTION 'claim_batches: lines are frozen exactly when the batch is submitted'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.status = 'void' AND unreleased > 0) OR (NEW.status <> 'void' AND released > 0) THEN
                RAISE EXCEPTION 'claim_batches: lines are released exactly when the batch is void'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_claim_batches_insert BEFORE INSERT ON claim_batches "
        "FOR EACH ROW EXECUTE FUNCTION fn_claim_batch_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_batches_update BEFORE UPDATE ON claim_batches "
        "FOR EACH ROW EXECUTE FUNCTION fn_claim_batch_update()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_claim_batches_ties_out AFTER INSERT OR UPDATE ON claim_batches "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fn_claim_batch_ties_out()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_batches_no_delete BEFORE DELETE ON claim_batches "
        "FOR EACH ROW EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_claim_batches_no_truncate BEFORE TRUNCATE ON claim_batches "
        "FOR EACH STATEMENT EXECUTE FUNCTION fn_forbid_mutation()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON claim_batches FROM rebate_app")
    op.execute(f"GRANT UPDATE ({', '.join(WORKFLOW)}) ON claim_batches TO rebate_app")
    enable_tenant_rls("claim_batches")

    op.execute(
        "ALTER TABLE claim_adjustments ADD CONSTRAINT fk_claim_adjustments_claim_batches "
        "FOREIGN KEY (tenant_id, included_in_batch_id) REFERENCES claim_batches (tenant_id, id)"
    )
