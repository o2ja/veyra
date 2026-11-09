"""rebate_owner may execute fn_user_memberships (operator runbook)

The grant in 20261109_2130 was made by rebate_owner to itself and disappeared when ownership moved to
rebate_lookup (found by the S1 runbook dry run). It must be granted by the new owner.

Revision ID: 20261110_0900_owner_lookup_exec
Revises: 20261109_2140_audit_idempotency
"""

from alembic import op

revision = "20261110_0900_owner_lookup_exec"
down_revision = "20261109_2140_audit_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rebate_lookup")
    op.execute("GRANT EXECUTE ON FUNCTION fn_user_memberships(uuid) TO rebate_owner")
    op.execute("RESET ROLE")
