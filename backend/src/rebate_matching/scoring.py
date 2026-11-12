"""Fuzzy score of a (remittance line, claim line) pair (BUSINESS_LOGIC.md §10.2). No floats, no external lib.

String similarity is the Indel ratio ``2*LCS/(len a + len b)`` (what rapidfuzz ``ratio`` computes; on normalised
identifiers, which contain no spaces, ``token_sort_ratio`` is the same thing). A missing field on either side
scores 0 for that component. Components are rounded to 6 places (half-up) and the total is their weighted sum, so
the recorded evidence reproduces the score exactly.
"""

from decimal import ROUND_HALF_UP, Decimal

from rebate_matching.config import MatchConfig
from rebate_matching.models import ClaimLine, RemittanceLine
from rebate_matching.normalise import normalise_ref, normalise_sku

_Q = Decimal("0.000001")
_ONE = Decimal(1)
_ZERO = Decimal(0)


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


def similarity(a: str, b: str) -> Decimal:
    """Indel similarity in [0, 1]; 0 if either string is empty."""
    if not a or not b:
        return _ZERO
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return _q(Decimal(2 * prev[-1]) / Decimal(len(a) + len(b)))


def date_score(days_apart: int, cfg: MatchConfig) -> Decimal:
    d = abs(days_apart)
    if d <= cfg.date_full_days:
        return _ONE
    if d >= cfg.date_zero_days:
        return _ZERO
    return _q(Decimal(cfg.date_zero_days - d) / Decimal(cfg.date_zero_days - cfg.date_full_days))


def fuzzy_score(
    rem: RemittanceLine, claim: ClaimLine, cfg: MatchConfig, aliases: dict[str, str]
) -> tuple[Decimal, dict[str, str]]:
    """Return ``(total, components)``; components are decimal strings for the evidence JSON."""
    invoice = similarity(normalise_ref(rem.invoice_no), normalise_ref(claim.invoice_no))
    rsku, csku = normalise_sku(rem.sku, aliases), normalise_sku(claim.sku, aliases)
    sku = _ONE if rsku and rsku == csku else similarity(rsku, csku)
    qty = _ONE if rem.quantity is not None and rem.quantity == claim.quantity else _ZERO
    net = claim.net_amount
    delta = abs(rem.amount_paid - net)
    if net == 0:
        amount = _ONE if delta == 0 else _ZERO
    else:
        amount = _q(_ONE - min(_ONE, delta / abs(net)))
    if rem.date is None or claim.date is None:
        date = _ZERO
    else:
        date = date_score((rem.date - claim.date).days, cfg)
    w = cfg.weights
    total = w.invoice * invoice + w.sku * sku + w.quantity * qty + w.amount * amount + w.date * date
    comps = {"invoice": invoice, "sku": sku, "quantity": qty, "amount": amount, "date": date, "total": total}
    return total, {k: str(v) for k, v in comps.items()}
