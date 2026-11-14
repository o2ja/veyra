"""Source-change hook for claimed lines (DATABASE_SCHEMA.md §5, BUSINESS_LOGIC.md §7 cancellations).

When an import supersedes a source line with a changed version, the loader asks this hook which of the
superseded lines are claimed. The new version of each such line gets ``source_changed = true`` (persisted, never
silent); the claims module then compares it with the frozen claim (recalculation proposal) and the missed-claims
scan refuses to offer it again (BUSINESS_LOGIC.md §9.4).

"Claimed" = the line, or any earlier version of it, is in a non-released claim line (draft, approved or submitted
batch). Plain SQL on ``claim_lines``: ``claims`` imports ``ingestion``, so the dependency cannot point back.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def claimed_line_ids(db: Session, tenant_id: UUID, line_ids: list[UUID]) -> frozenset[UUID]:
    """Which of ``line_ids`` are in an active (non-released) claim line, directly or through an earlier version."""
    if not line_ids:
        return frozenset()
    rows = db.scalars(
        text(
            "WITH RECURSIVE anc(line_id, source_id) AS ("
            "SELECT x, x FROM unnest(CAST(:ids AS uuid[])) AS x "
            "UNION ALL SELECT anc.line_id, s.id FROM anc "
            "JOIN sales_document_lines s ON s.tenant_id = :t AND s.superseded_by_id = anc.source_id) "
            "SELECT DISTINCT anc.line_id FROM anc JOIN claim_lines c ON c.tenant_id = :t "
            "AND c.sales_document_line_id = anc.source_id AND c.released_at IS NULL"
        ),
        {"t": tenant_id, "ids": line_ids},
    )
    return frozenset(rows)
