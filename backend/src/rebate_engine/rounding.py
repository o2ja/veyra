"""Per-line and per-document rounding (BUSINESS_LOGIC.md §6)."""

from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal, localcontext

from rebate_engine.models import RoundingRule
from rebate_engine.money import MONEY_CONTEXT, quantize


def round_line(raw: Decimal, rule: RoundingRule) -> Decimal:
    return quantize(raw, rule.scale, rule.mode)


def allocate_document(raws: Sequence[Decimal], rule: RoundingRule) -> list[Decimal]:
    """Round the full-precision document total, then allocate it back to the lines by largest remainder.

    Each line starts at its floor on the scale grid; the units still missing to reach the rounded total go
    one each to the lines with the largest remainders, ties broken by line order (the order of ``raws``).
    The result always sums exactly to ``round(sum raws)``.
    """
    with localcontext(MONEY_CONTEXT):
        unit = Decimal(1).scaleb(-rule.scale)
        total = quantize(sum(raws, Decimal(0)), rule.scale, rule.mode)
        floors = [r.quantize(unit, rounding=ROUND_FLOOR) for r in raws]
        missing = int((total - sum(floors, Decimal(0))) / unit)
        if not 0 <= missing <= len(raws):
            raise AssertionError("largest-remainder invariant violated")  # pragma: no cover - math bound
        by_remainder = sorted(range(len(raws)), key=lambda i: (-(raws[i] - floors[i]), i))
        bumped = set(by_remainder[:missing])
        return [f + unit if i in bumped else f for i, f in enumerate(floors)]
