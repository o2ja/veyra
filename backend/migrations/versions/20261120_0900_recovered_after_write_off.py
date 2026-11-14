"""S10 recovered_after_write_off (owner decision 2026-11-13, D-N5): money paid on a reclaim after the case was written
off is reported as its own figure; closed cases stay immutable.

Revision ID: 20261120_0900_after_write_off
Revises: 20261119_0900_summary_monthly
"""

from alembic import op

revision = "20261120_0900_after_write_off"
down_revision = "20261119_0900_summary_monthly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE summary_claims_monthly ADD COLUMN recovered_after_write_off numeric(18, 4) NOT NULL DEFAULT 0"
    )
