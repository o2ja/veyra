"""audit_log (append-only, RLS) and idempotency_keys (RLS)

Revision ID: 20261109_2140_audit_idempotency
Revises: 20261109_2130_identity_tenancy
"""

from alembic import op

from migrations._audit import AUDIT_TABLE
from migrations._immutable import make_append_only
from migrations._rls import enable_tenant_rls

revision = "20261109_2140_audit_idempotency"
down_revision = "20261109_2130_identity_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {AUDIT_TABLE} (
            tenant_id uuid NOT NULL,
            id bigint GENERATED ALWAYS AS IDENTITY,
            occurred_at timestamptz NOT NULL,
            actor_type text NOT NULL,
            actor_user_id uuid,
            action text NOT NULL,
            entity_type text NOT NULL,
            entity_id uuid,
            before jsonb,
            after jsonb,
            request_id text,
            ip inet,
            user_agent text,
            metadata jsonb,
            CONSTRAINT pk_audit_log PRIMARY KEY (id),
            CONSTRAINT uq_audit_log_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_audit_log_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_audit_log_users FOREIGN KEY (actor_user_id) REFERENCES users (id),
            CONSTRAINT ck_audit_log_actor_type CHECK (actor_type IN ('user', 'system', 'ai')),
            CONSTRAINT ck_audit_log_user_actor CHECK (actor_type <> 'user' OR actor_user_id IS NOT NULL),
            CONSTRAINT ck_audit_log_action_format CHECK (action ~ '^[a-z_]+(\\.[a-z_]+)+$'),
            CONSTRAINT ck_audit_log_user_agent_length CHECK (char_length(user_agent) <= 512)
        )
        """
    )
    op.execute(f"CREATE INDEX ix_audit_log_entity ON {AUDIT_TABLE} (tenant_id, entity_type, entity_id)")
    op.execute(f"CREATE INDEX ix_audit_log_occurred_at ON {AUDIT_TABLE} (tenant_id, occurred_at)")
    op.execute(f"CREATE INDEX ix_audit_log_actor ON {AUDIT_TABLE} (tenant_id, actor_user_id, occurred_at)")
    enable_tenant_rls(AUDIT_TABLE)
    make_append_only(AUDIT_TABLE)

    op.execute(
        """
        CREATE TABLE idempotency_keys (
            tenant_id uuid NOT NULL,
            key text NOT NULL,
            endpoint text NOT NULL,
            request_hash text NOT NULL,
            response_status integer NOT NULL,
            response_body jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            CONSTRAINT pk_idempotency_keys PRIMARY KEY (tenant_id, key, endpoint),
            CONSTRAINT fk_idempotency_keys_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_idempotency_keys_key_length CHECK (char_length(key) BETWEEN 1 AND 100),
            CONSTRAINT ck_idempotency_keys_endpoint_length CHECK (char_length(endpoint) <= 200)
        )
        """
    )
    enable_tenant_rls("idempotency_keys")
