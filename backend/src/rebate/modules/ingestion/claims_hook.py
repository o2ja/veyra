"""Source-change hook for claimed lines (DATABASE_SCHEMA.md §5, BUSINESS_LOGIC.md §7 cancellations).

When an import supersedes a source line with a changed version, the loader asks this hook which of the
superseded lines are referenced by a non-released claim line. The new version of each such line gets
``source_changed = true`` (persisted, never silent), which S7 turns into a ``source_changed`` variance
case and an adjustment proposal.

S3 state, documented and real: the ``claim_lines`` table does not exist before S7, so no source line
can be claimed yet and the honest answer is "none". S7 replaces the body with
``SELECT sales_document_line_id FROM claim_lines WHERE released_at IS NULL AND sales_document_line_id
= ANY(:ids)`` (through the claims service) and creates the cases. Nothing here fakes a claim.
"""

from uuid import UUID

from sqlalchemy.orm import Session


def claimed_line_ids(db: Session, tenant_id: UUID, line_ids: list[UUID]) -> frozenset[UUID]:
    """Which of ``line_ids`` are in an active (non-released) claim line. No claims exist before S7."""
    del db, tenant_id, line_ids
    return frozenset()
