"""users.last_failed_at: failed-login counters expire like those of unknown emails (S1 review)

Revision ID: 20261110_1000_users_last_failed
Revises: 20261110_0900_owner_lookup_exec
"""

from alembic import op

revision = "20261110_1000_users_last_failed"
down_revision = "20261110_0900_owner_lookup_exec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN last_failed_at timestamptz")
