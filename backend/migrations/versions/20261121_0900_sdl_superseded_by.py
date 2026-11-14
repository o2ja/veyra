"""Index for the missed-claims ancestor walk (NFR-04): ``UNCLAIMED`` follows ``superseded_by_id`` backwards from a line
to its earlier versions; without this index every probe seq-scans ``sales_document_lines``. Partial: only superseded
rows carry a value, so it stays tiny.

Revision ID: 20261121_0900_sdl_superseded_by
Revises: 20261120_0900_after_write_off
"""

from alembic import op

revision = "20261121_0900_sdl_superseded_by"
down_revision = "20261120_0900_after_write_off"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_sales_document_lines_tenant_id_superseded_by_id ON sales_document_lines "
        "(tenant_id, superseded_by_id) WHERE superseded_by_id IS NOT NULL"
    )
