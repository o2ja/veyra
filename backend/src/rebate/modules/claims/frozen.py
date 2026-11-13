# ruff: noqa: S608  # SQL assembled from module constants only; every value is a bound parameter
"""Frozen claims (``frozen_at`` set, not released), and the live claims (any batch status) that use up caps, as the calculation needs them (BUSINESS_LOGIC.md §4.2, §7, §9.3).

``prior_claims`` and ``frozen_claims`` read frozen lines only (draft lines are not claims yet); ``consumption`` reads every live line. A re-imported source line is a new version
(``superseded_by_id``); a claim stays attached to the version it was made on, so every lookup by the *current* line
id follows the version chain back to the claimed version. ``frozen_claims`` therefore answers for the changed line
too, which is what turns a source change into a recalculation proposal instead of a second claim.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rebate.modules import agreements, masterdata
from rebate_engine.models import PriorClaim, UomConversion
from rebate_engine.rules import parse_spa_rules
from rebate_engine.uom import find_factor

ZERO = Decimal(0)


@dataclass(frozen=True)
class FrozenClaim:
    """The frozen (not released) claim line of a source sales line."""

    claim_line_id: UUID
    sales_line_id: UUID
    manufacturer_id: UUID
    claim_amount: Decimal


class FrozenClaimSource(Protocol):
    def prior_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, PriorClaim]:
        """Per original sales line: the engine view of its active claim line (agreement, UoM, quantity, unit)."""
        ...

    def consumption(
        self,
        db: Session,
        tenant_id: UUID,
        exclude_line_ids: Collection[UUID],
        data_cutoff: datetime | None = None,
    ) -> Mapping[tuple[str, str], Decimal]:
        """(agreement_id, product_id) -> quantity consumed by active claim lines net of returns (returns imported by
        ``data_cutoff``), EXCLUDING claims whose source line is in ``exclude_line_ids`` (the lines of the run)."""
        ...

    def frozen_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, FrozenClaim]:
        """Per sales line: its active claim line, for recalculation proposals (§9.3)."""
        ...


# line_id (as asked) -> source_id (that line or any earlier version of it)
_ANCESTRY = (
    "WITH RECURSIVE anc(line_id, source_id) AS ("
    "SELECT x, x FROM unnest(CAST(:ids AS uuid[])) AS x "
    "UNION ALL SELECT anc.line_id, s.id FROM anc "
    "JOIN sales_document_lines s ON s.tenant_id = :t AND s.superseded_by_id = anc.source_id) "
)
# what the frozen claim was netted by: the "returns" step of the calculation result the claim was made from
# (owner decision 2026-11-13, BUSINESS_LOGIC §7)
_NETTED = (
    "(SELECT CAST(s->>'returned_quantity' AS numeric) FROM jsonb_array_elements(r.trace->'steps') s "
    "WHERE s->>'step' = 'returns' LIMIT 1)"
)
_ACTIVE = "c.released_at IS NULL AND c.frozen_at IS NOT NULL AND c.source_kind = 'sales_line'"
# the cap counts every live claim line (draft, approved or submitted batch), BUSINESS_LOGIC §4.2
_LIVE = "c.released_at IS NULL AND c.source_kind = 'sales_line'"


class SqlFrozenClaims:
    """The ``claim_lines`` reader."""

    def _rows(self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]) -> list[Any]:
        return list(
            db.execute(
                text(
                    _ANCESTRY
                    + "SELECT anc.line_id, c.id AS claim_line_id, c.claim_amount, c.quantity, c.unit_amount, "  # nosec B608 (module constants, bound values)
                    "c.agreement_version_id, r.agreement_id, r.claim_uom, r.manufacturer_id, "
                    + _NETTED
                    + " AS netted "
                    "FROM anc JOIN claim_lines c ON c.tenant_id = :t AND c.sales_document_line_id = anc.source_id "
                    "JOIN calculation_results r ON r.tenant_id = c.tenant_id AND r.id = c.calculation_result_id "
                    "WHERE " + _ACTIVE
                ),
                {"t": tenant_id, "ids": list(line_ids)},
            )
        )

    def prior_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, PriorClaim]:
        if not line_ids:
            return {}
        rows = self._rows(db, tenant_id, line_ids)
        rules = agreements.version_rules(db, tenant_id, sorted({r.agreement_version_id for r in rows}))
        if any(r.netted is None for r in rows):  # every claimed result carries its netting basis: fail loudly
            raise ValueError("claimed calculation result without a 'returns' trace step")
        return {
            r.line_id: PriorClaim(
                agreement_id=str(r.agreement_id),
                agreement_version_id=str(r.agreement_version_id),
                claim_uom=r.claim_uom,
                quantity=r.quantity,
                unit_amount=r.unit_amount,
                claim_amount=r.claim_amount,
                rounding=parse_spa_rules(rules[r.agreement_version_id]).rounding,
                netted_returned_quantity=r.netted,
            )
            for r in rows
        }

    def frozen_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, FrozenClaim]:
        if not line_ids:
            return {}
        return {
            r.line_id: FrozenClaim(r.claim_line_id, r.line_id, r.manufacturer_id, r.claim_amount)
            for r in self._rows(db, tenant_id, line_ids)
        }

    def consumption(
        self,
        db: Session,
        tenant_id: UUID,
        exclude_line_ids: Collection[UUID],
        data_cutoff: datetime | None = None,
    ) -> Mapping[tuple[str, str], Decimal]:
        # ponytail: one scan of the tenant's live claims per manufacturer window; add a cap-state projection
        # if S11 measures it slow. Returns are those linked to the claimed version itself.
        rows = db.execute(
            text(
                "WITH RECURSIVE ch(claim_id, line_id) AS ("  # nosec B608 (module constants, bound values)
                "SELECT c.id, c.sales_document_line_id FROM claim_lines c WHERE c.tenant_id = :t AND "
                + _LIVE
                + " UNION ALL SELECT ch.claim_id, s.superseded_by_id FROM ch "  # nosec B608 (module constants, bound values)
                "JOIN sales_document_lines s ON s.tenant_id = :t AND s.id = ch.line_id "
                "WHERE s.superseded_by_id IS NOT NULL) "
                "SELECT c.quantity, r.agreement_id, r.claim_uom, o.product_id, o.uom, "
                "COALESCE((SELECT sum(-l.quantity) FROM sales_document_lines l "
                "JOIN import_batches b ON b.tenant_id = l.tenant_id AND b.id = l.import_batch_id "
                "AND b.status IN ('completed', 'completed_with_errors') "
                "AND (CAST(:cutoff AS timestamptz) IS NULL OR b.completed_at <= :cutoff) "
                "WHERE l.tenant_id = c.tenant_id AND l.is_current AND l.quantity < 0 "
                "AND l.original_line_id = c.sales_document_line_id "
                "AND (CAST(:cutoff AS timestamptz) IS NULL OR l.invoice_date <= CAST(:cutoff AS date))), 0) "
                "AS returned, " + _NETTED + " AS netted "
                "FROM claim_lines c JOIN calculation_results r ON r.tenant_id = c.tenant_id "
                "AND r.id = c.calculation_result_id "
                "JOIN sales_document_lines o ON o.tenant_id = c.tenant_id AND o.id = c.sales_document_line_id "
                "WHERE c.tenant_id = :t AND "
                + _LIVE
                + " AND c.id NOT IN (SELECT claim_id FROM ch WHERE line_id = ANY(CAST(:ex AS uuid[]))) "  # nosec B608 (module constants, bound values)
                "AND o.product_id IS NOT NULL"
            ),
            {"t": tenant_id, "ex": list(exclude_line_ids), "cutoff": data_cutoff},
        ).all()
        if not rows:
            return {}
        if any(r.netted is None for r in rows):
            raise ValueError("claimed calculation result without a 'returns' trace step")
        conversions = tuple(
            UomConversion(
                product_id=None if c.product_id is None else str(c.product_id),
                from_uom=c.from_uom,
                to_uom=c.to_uom,
                factor=c.factor,
            )
            for c in masterdata.uom_conversion_rows(db, tenant_id)
        )
        out: dict[tuple[str, str], Decimal] = {}
        for r in rows:
            factor = find_factor(conversions, str(r.product_id), r.uom, r.claim_uom)
            # only returns the claim was not netted by reduce what it consumes
            returned = max(r.returned - r.netted, ZERO) * factor if factor is not None else ZERO
            key = (str(r.agreement_id), str(r.product_id))
            out[key] = out.get(key, ZERO) + max(r.quantity - returned, ZERO)
        return out


source: FrozenClaimSource = SqlFrozenClaims()
