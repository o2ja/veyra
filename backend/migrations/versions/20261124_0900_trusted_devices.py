"""Trust this device for 2FA, add trusted_devices (global credential table, no RLS).

Revision ID: 20261124_0900_trusted_devices
Revises: 20261123_0900_phase1_safe_delete
"""

from alembic import op

revision = "20261124_0900_trusted_devices"
down_revision = "20261123_0900_phase1_safe_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE trusted_devices (
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            tenant_id uuid,
            token_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            CONSTRAINT pk_trusted_devices PRIMARY KEY (id),
            CONSTRAINT uq_trusted_devices_token_hash UNIQUE (token_hash),
            CONSTRAINT fk_trusted_devices_users FOREIGN KEY (user_id) REFERENCES users (id),
            CONSTRAINT fk_trusted_devices_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id)
        )
        """
    )
    op.execute("CREATE INDEX ix_trusted_devices_user_id ON trusted_devices (user_id)")
    op.execute("REVOKE ALL ON trusted_devices FROM rebate_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE trusted_devices")
