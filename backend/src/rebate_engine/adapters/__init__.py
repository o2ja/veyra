"""Named manufacturer adapters (BUSINESS_LOGIC.md §1.7, `rules.adapter`): mechanism only, registry empty.

An adapter replaces the basis formula for the claim unit amount. It receives the line, the agreement item
and the resolved per-claim-UoM values, and returns the unit amount; the engine still applies every gate,
cap, rounding and the claim window, and records the adapter name in the trace.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from rebate_engine.models import EngineRuleError, SalesLine, SpaItem


@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterInput:
    line: SalesLine
    item: SpaItem
    claim_uom: str
    factor: Decimal  # line qty x factor = qty in claim UoM
    reference_cost: Decimal | None  # per claim UoM
    unit_price: Decimal  # per claim UoM


type Adapter = Callable[[AdapterInput], Decimal]

REGISTRY: dict[str, Adapter] = {}


class UnknownAdapter(EngineRuleError):
    pass


def get_adapter(name: str) -> Adapter:
    if name not in REGISTRY:
        raise UnknownAdapter(f"adapter not registered: {name!r}")
    return REGISTRY[name]
