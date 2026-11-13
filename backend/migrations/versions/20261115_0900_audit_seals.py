"""audit_seals (append-only hash chain over audit_log, ADR-026) and the first seal job per tenant

Revision ID: 20261115_0900_audit_seals
Revises: 20261114_0900_calc_runs

No foreign keys to audit_log on purpose: a later, audited retention purge removes old entries and the seals
that cover them together; the verification tool checks that every sealed range still matches.
Forward-only like the rest of the chain (no downgrade function).
"""

from alembic import op

from migrations._immutable import make_append_only
from migrations._rls import enable_tenant_rls

revision = "20261115_0900_audit_seals"
down_revision = "20261114_0900_calc_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_seals (
            tenant_id uuid NOT NULL,
            id bigint GENERATED ALWAYS AS IDENTITY,
            from_audit_id bigint NOT NULL,
            to_audit_id bigint NOT NULL,
            prev_hash char(64),
            seal_hash char(64) NOT NULL,
            sealed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_audit_seals PRIMARY KEY (id),
            CONSTRAINT uq_audit_seals_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_audit_seals_tenant_id_from_audit_id UNIQUE (tenant_id, from_audit_id),
            CONSTRAINT uq_audit_seals_tenant_id_to_audit_id UNIQUE (tenant_id, to_audit_id),
            CONSTRAINT fk_audit_seals_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_audit_seals_range CHECK (from_audit_id <= to_audit_id),
            CONSTRAINT ck_audit_seals_prev_hash CHECK (prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_audit_seals_seal_hash CHECK (seal_hash ~ '^[0-9a-f]{64}$')
        )
        """
    )
    enable_tenant_rls("audit_seals")
    make_append_only("audit_seals")
    # Existing workspaces: start their self-rescheduling seal chain (new ones start in create_workspace).
    # The owner role is subject to the FORCEd tenants policy (no tenant context in a migration), so FORCE is
    # lifted for this statement only; DDL is transactional, so it cannot stay off.
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts, dedupe_key)
        SELECT gen_random_uuid(), id, 'audit_seal', '{}'::jsonb, 5, format('audit_seal%s%s%sbootstrap', chr(58), id, chr(58))
        FROM tenants
        """
    )
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
