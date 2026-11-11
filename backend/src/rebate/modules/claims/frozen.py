"""Frozen (submitted) claims as the calculation needs them (BUSINESS_LOGIC.md §4.2, §7, §9.3).

``claim_lines`` and claim batches arrive with S7. Until then no source line can be claimed, so the only source
there is answers "nothing" honestly (``NoFrozenClaims``). S7 replaces ``frozen_claims`` with a source that reads
``claim_lines``; the run orchestration and the recalculation proposal below do not change.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from rebate_engine.models import PriorClaim


@dataclass(frozen=True)
class FrozenClaim:
    """The active (not released) claim line of a source sales line."""

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
        self, db: Session, tenant_id: UUID, exclude_line_ids: Collection[UUID]
    ) -> Mapping[tuple[str, str], Decimal]:
        """(agreement_id, product_id) -> quantity consumed by active claim lines net of return adjustments,
        EXCLUDING claims whose source line is in ``exclude_line_ids`` (the lines of the run)."""
        ...

    def frozen_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, FrozenClaim]:
        """Per sales line: its active claim line, for recalculation proposals (§9.3)."""
        ...


class NoFrozenClaims:
    """S5 state: no claim lines exist yet."""

    def prior_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, PriorClaim]:
        return {}

    def consumption(
        self, db: Session, tenant_id: UUID, exclude_line_ids: Collection[UUID]
    ) -> Mapping[tuple[str, str], Decimal]:
        return {}

    def frozen_claims(
        self, db: Session, tenant_id: UUID, line_ids: Sequence[UUID]
    ) -> Mapping[UUID, FrozenClaim]:
        return {}


source: FrozenClaimSource = NoFrozenClaims()  # S7 swaps this for the claim_lines reader
