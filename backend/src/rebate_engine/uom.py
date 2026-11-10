"""Unit-of-measure conversion lookup (BUSINESS_LOGIC.md §4.0 gate 5)."""

from collections.abc import Iterable
from decimal import Decimal, localcontext

from rebate_engine.models import UomConversion
from rebate_engine.money import MONEY_CONTEXT


def find_factor(
    conversions: Iterable[UomConversion], product_id: str | None, from_uom: str, to_uom: str
) -> Decimal | None:
    """Quantity factor so that ``qty_in_to = qty_in_from x factor``; None when no conversion exists.

    Lookup order: product-specific, then tenant default; at each level direct before inverse.
    """
    if from_uom == to_uom:
        return Decimal(1)
    convs = tuple(conversions)
    for owner in dict.fromkeys((product_id, None)):
        level = [c for c in convs if c.product_id == owner]
        for c in level:
            if (c.from_uom, c.to_uom) == (from_uom, to_uom):
                return c.factor
        for c in level:
            if (c.from_uom, c.to_uom) == (to_uom, from_uom):
                with localcontext(MONEY_CONTEXT):
                    return Decimal(1) / c.factor
    return None
