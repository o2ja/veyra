"""Hypothesis properties of ``reconcile`` (BUSINESS_LOGIC.md §10.2 invariants)."""

from collections import Counter
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from rebate_matching.engine import reconcile
from rebate_matching.models import ClaimLine, Match, Method, RemittanceLine, Status

MFRS = st.sampled_from(["A", "B"])
INVS = st.sampled_from(["INV-1", "inv 2", "INV3", "0007"])
cents = st.integers(min_value=1, max_value=50_000).map(lambda c: Decimal(c) / 100)


@st.composite
def scenario(draw: st.DrawFn) -> tuple[list[RemittanceLine], list[ClaimLine]]:
    nc = draw(st.integers(0, 8))
    nr = draw(st.integers(0, 6))
    claims = [
        ClaimLine(
            f"c{i:02d}",
            draw(MFRS),
            f"CL-{i}",
            draw(INVS),
            draw(st.sampled_from([None, 1, 2])),
            draw(cents),
            sku=draw(st.sampled_from([None, "S1", "S2"])),
            quantity=draw(st.sampled_from([None, Decimal(1), Decimal(2)])),
        )
        for i in range(nc)
    ]
    rems = [
        RemittanceLine(
            f"r{i:02d}",
            draw(MFRS),
            draw(cents),
            claim_reference=draw(st.sampled_from([None, "cl-0", "CL-1", "x"])),
            invoice_no=draw(st.sampled_from([None, *["INV-1", "inv 2", "INV3", "7"]])),
            invoice_line=draw(st.sampled_from([None, 1, 2])),
            sku=draw(st.sampled_from([None, "S1", "S2"])),
            quantity=draw(st.sampled_from([None, Decimal(1), Decimal(2)])),
        )
        for i in range(nr)
    ]
    return rems, claims


def _key(m: Match) -> tuple[object, ...]:
    return (m.remittance_line_id, m.method, m.status, m.allocations, m.auto_confirm)


@settings(max_examples=150, deadline=None)
@given(scenario())
def test_allocation_invariants(s: tuple[list[RemittanceLine], list[ClaimLine]]) -> None:
    rems, claims = s
    res = reconcile(rems, claims)
    r_by = {r.id: r for r in rems}
    c_by = {c.id: c for c in claims}
    per_rem = Counter[str]()
    used_claims = Counter[str]()
    for m in res.matches:
        per_rem[m.remittance_line_id] += 1
        assert m.allocated <= r_by[m.remittance_line_id].amount_paid  # remittance side
        for a in m.allocations:
            assert c_by[a.claim_line_id].manufacturer == r_by[m.remittance_line_id].manufacturer
        for cid in m.claim_line_ids:
            used_claims[cid] += 1
            if (
                r_by[m.remittance_line_id].amount_paid <= c_by[cid].net_amount
                or m.method is Method.SUBSET_SUM
            ):
                assert sum(a.amount for a in m.allocations if a.claim_line_id == cid) <= c_by[cid].net_amount
        if m.method is Method.SUBSET_SUM:  # conservation: Σ allocations == amount_paid
            assert m.allocated == r_by[m.remittance_line_id].amount_paid
        if m.auto_confirm:
            assert m.status is Status.CONFIRMED and m.method in (
                Method.EXACT_REFERENCE,
                Method.INVOICE_LINE,
                Method.COMPOSITE,
            )
        if m.status is Status.SUGGESTED:
            assert not m.auto_confirm
    assert all(n == 1 for n in per_rem.values())  # one match per remittance line from automatic tiers
    assert all(n == 1 for n in used_claims.values())  # a claim line is used by at most one automatic match
    assert set(res.unmatched_remittance_ids) == set(r_by) - set(per_rem)
    assert set(res.unmatched_claim_ids) == set(c_by) - set(used_claims)


@settings(max_examples=100, deadline=None)
@given(scenario(), st.randoms(use_true_random=False))
def test_deterministic_under_input_order(s: tuple[list[RemittanceLine], list[ClaimLine]], rnd) -> None:  # type: ignore[no-untyped-def]
    rems, claims = s
    base = reconcile(rems, claims)
    r2, c2 = list(rems), list(claims)
    rnd.shuffle(r2)
    rnd.shuffle(c2)
    again = reconcile(r2, c2)
    assert [_key(m) for m in again.matches] == [_key(m) for m in base.matches]
    assert again.unmatched_claim_ids == base.unmatched_claim_ids
    assert again.notes == base.notes


@settings(max_examples=100, deadline=None)
@given(scenario())
def test_rerun_never_changes_confirmed(s: tuple[list[RemittanceLine], list[ClaimLine]]) -> None:
    rems, claims = s
    first = reconcile(rems, claims)
    confirmed = [m for m in first.matches if m.status is Status.CONFIRMED]
    second = reconcile(rems, claims, existing=first.matches)
    assert all(m in second.matches for m in confirmed)
    third = reconcile(rems, claims, existing=second.matches)
    assert [_key(m) for m in third.matches if m.status is Status.CONFIRMED] == [
        _key(m) for m in second.matches if m.status is Status.CONFIRMED
    ]
