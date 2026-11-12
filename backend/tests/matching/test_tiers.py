"""Tier behaviour, ambiguity, fuzzy scoring, subset-sum limits, re-run and manual matches (hand-derived values)."""

from datetime import date
from decimal import Decimal

import pytest

from rebate_matching.assignment import maximise_assignment, unique_subset_sum
from rebate_matching.config import MatchConfig
from rebate_matching.engine import InvariantViolation, confirm_suggestion, manual_match, reconcile
from rebate_matching.models import Allocation, ClaimLine, Match, Method, RemittanceLine, Status
from rebate_matching.scoring import fuzzy_score, similarity

D = Decimal


def claim(i: str, amount: str, inv: str = "INV-1", line: int | None = 1, **kw: object) -> ClaimLine:
    return ClaimLine(i, "M", f"CL-{i}", inv, line, D(amount), **kw)  # type: ignore[arg-type]


def rem(i: str, paid: str, **kw: object) -> RemittanceLine:
    return RemittanceLine(i, "M", D(paid), **kw)  # type: ignore[arg-type]


def test_t1_reference_beats_wrong_invoice() -> None:
    res = reconcile([rem("r1", "10", claim_reference=" cl-0a ", invoice_no="INV-9")], [claim("0A", "10")])
    (m,) = res.matches
    assert (m.method, m.status, m.auto_confirm) == (Method.EXACT_REFERENCE, Status.CONFIRMED, True)


def test_t2_two_claims_same_invoice_line_is_ambiguous_not_confirmed() -> None:
    claims = [claim("a", "10"), claim("b", "10")]
    res = reconcile([rem("r1", "10", invoice_no="inv 1", invoice_line=1)], claims)
    assert all(not m.auto_confirm and m.status is Status.SUGGESTED for m in res.matches)
    assert not any(m.method is Method.INVOICE_LINE for m in res.matches)


def test_two_remittances_same_claim_neither_auto_confirmed() -> None:
    res = reconcile(
        [
            rem("r1", "10", invoice_no="INV-1", invoice_line=1),
            rem("r2", "10", invoice_no="INV-1", invoice_line=1),
        ],
        [claim("a", "10")],
    )
    assert not any(m.auto_confirm for m in res.matches)


def test_t2_ignores_other_manufacturer() -> None:
    other = ClaimLine("x", "OTHER", "CL-x", "INV-1", 1, D("10"))
    res = reconcile([rem("r1", "10", invoice_no="INV-1", invoice_line=1)], [other])
    assert res.matches == () and res.unmatched_remittance_ids == ("r1",)


def test_t3_equal_amount_confirmed_otherwise_suggested() -> None:
    c = claim("a", "100", inv="INV-5", line=None, sku="X-1", quantity=D("4"))
    exact = reconcile([rem("r", "100", invoice_no="inv5", sku="x1", quantity=D("4"))], [c])
    assert (exact.matches[0].method, exact.matches[0].status) == (Method.COMPOSITE, Status.CONFIRMED)
    near = reconcile(
        [rem("r", "96", invoice_no="inv5", sku="x1", quantity=D("4"))], [c]
    )  # 4 <= 5% of 100? 4% yes
    assert (near.matches[0].method, near.matches[0].status, near.matches[0].auto_confirm) == (
        Method.COMPOSITE,
        Status.SUGGESTED,
        False,
    )
    far = reconcile([rem("r", "80", invoice_no="inv5", sku="x1", quantity=D("4"))], [c])  # 20 > max(5, 5)
    assert all(m.method is not Method.COMPOSITE for m in far.matches)


def test_t3_sku_alias_and_absolute_tolerance() -> None:
    c = claim("a", "20", inv="INV-5", line=None, sku="OLD-SKU", quantity=D("1"))
    res = reconcile(
        [rem("r", "15.50", invoice_no="INV5", sku="new sku", quantity=D("1"))],
        [c],
        sku_aliases={"OLDSKU": "NEWSKU"},
    )  # |4.50| > 5% of 20 (=1) but <= $5
    assert res.matches[0].method is Method.COMPOSITE


def test_similarity_hand_values() -> None:
    assert similarity("INV10001", "INV10001") == D("1")
    assert similarity("INV99999", "INV10001") == D("0.375")  # LCS "INV" = 3 -> 2*3/16
    assert similarity("", "ABC") == D("0")


def test_fuzzy_components_and_threshold() -> None:
    c = claim("a", "100", inv="INV-100", line=None, sku="S1", quantity=D("2"), date=date(2026, 7, 1))
    r = rem("r", "100", invoice_no="INV-101", sku="S1", quantity=D("2"), date=date(2026, 7, 10))
    # hand: invoice LCS("INV100","INV101")=5 -> 10/12=0.833333; sku 1; qty 1; amount 1; date 9d -> (30-9)/27=0.777778
    total, comps = fuzzy_score(r, c, MatchConfig(), {})
    assert comps["invoice"] == "0.833333" and comps["date"] == "0.777778"
    assert total == D("0.35") * D("0.833333") + D("0.25") + D("0.15") + D("0.15") + D("0.10") * D("0.777778")
    res = reconcile([r], [c])
    (m,) = res.matches
    assert (m.method, m.status, m.auto_confirm) == (Method.FUZZY, Status.SUGGESTED, False)
    assert m.evidence["components"] == comps and m.evidence["score"] == str(total)
    below = reconcile([rem("r", "90", invoice_no="INV-101", sku="S2", quantity=D("9"))], [c])
    assert below.matches == ()


def test_fuzzy_tie_is_flagged_never_confirmed() -> None:
    cs = [
        claim("a", "100", inv="INV-100", line=None, sku="S", quantity=D("2")),
        claim("b", "100", inv="INV-100", line=None, sku="S", quantity=D("2")),
    ]
    res = reconcile([rem("r", "100", invoice_no="INV-100", sku="S", quantity=D("2"))], cs)
    # same invoice/sku/qty/amount: T3 sees two candidates -> falls to fuzzy
    (m,) = res.matches
    assert m.method is Method.FUZZY and not m.auto_confirm
    assert m.evidence["tied_with"] == ["b"] and m.allocations[0].claim_line_id == "a"  # tie -> lowest id


def test_hungarian_is_optimal_not_greedy() -> None:
    # greedy would take (0,0)=10 then (1,1)=1 => 11; optimum is (0,1)+(1,0)=9+9=18
    assert maximise_assignment([[10, 9], [9, 1]]) == [(0, 1), (1, 0)]
    assert maximise_assignment([[5, 0, 0]]) == [(0, 0)]  # rectangular
    assert maximise_assignment([[0], [7], [3]]) == [(1, 0)]  # transposed
    assert maximise_assignment([[0, 0], [0, 0]]) == []


def test_subset_sum_unique_ambiguous_none() -> None:
    a = [D("1000"), D("1100"), D("660")]
    assert unique_subset_sum(a, D("2100"), 12, 100_000) == ("unique", (0, 1))
    assert unique_subset_sum(a, D("75"), 12, 100_000) == ("none", ())
    assert (
        unique_subset_sum([D("5"), D("5"), D("10")], D("10"), 12, 100_000)[0] == "ambiguous"
    )  # {10} and {5,5}


def test_subset_sum_limit_reports_instead_of_guessing() -> None:
    amounts = [D(1000 + i) for i in range(30)]  # many sums; target reachable only deep in the search
    status, idx = unique_subset_sum(amounts, sum(amounts[:12], D(0)), 12, 50)
    assert (status, idx) == ("limit", ())
    # and via reconcile: a note, no match
    cs = [claim(f"{i:02d}", str(1000 + i), inv=f"INV-{i}") for i in range(30)]
    res = reconcile(
        [rem("r", str(sum(1000 + i for i in range(12))))], cs, config=MatchConfig(subset_max_combinations=50)
    )
    assert res.matches == ()
    assert [(n.tier, n.code) for n in res.notes] == [(Method.SUBSET_SUM, "combination_limit_reached")]


def test_subset_sum_size_bound() -> None:
    amounts = [D("1")] * 13
    assert unique_subset_sum(amounts, D("13"), 12, 100_000)[0] == "none"


def test_t5_suggested_only_with_split_allocations() -> None:
    cs = [claim("a", "1000", inv="INV-A"), claim("b", "1100", inv="INV-B"), claim("c", "660", inv="INV-C")]
    res = reconcile([rem("r", "2100")], cs)
    (m,) = res.matches
    assert (m.method, m.status, m.auto_confirm) == (Method.SUBSET_SUM, Status.SUGGESTED, False)
    assert [(a.claim_line_id, a.amount) for a in m.allocations] == [("a", D("1000")), ("b", D("1100"))]
    assert res.unmatched_claim_ids == ("c",)


def test_t5_ambiguous_yields_note_not_match() -> None:
    cs = [claim("a", "5", inv="INV-A"), claim("b", "5", inv="INV-B"), claim("c", "10", inv="INV-C")]
    res = reconcile([rem("r", "10")], cs)
    assert [m.method for m in res.matches] == [] or all(
        m.method is not Method.SUBSET_SUM for m in res.matches
    )
    assert [(n.code) for n in res.notes] == ["ambiguous_subset_sum"]


def test_rerun_preserves_confirmed_and_drops_suggestions() -> None:
    cs = [claim("a", "10", inv="INV-A"), claim("b", "20", inv="INV-B")]
    first = reconcile([rem("r1", "10", invoice_no="INV-A", invoice_line=1)], cs)
    confirmed = first.matches[0]
    stale = Match("r2", Method.FUZZY, Status.SUGGESTED, (Allocation("b", D("20")),))
    second = reconcile(
        [
            rem("r1", "10", invoice_no="INV-A", invoice_line=1),
            rem("r2", "20", invoice_no="INV-B", invoice_line=1),
        ],
        cs,
        existing=[confirmed, stale],
    )
    assert confirmed in second.matches
    assert stale not in second.matches
    assert {m.remittance_line_id: m.method for m in second.matches}["r2"] is Method.INVOICE_LINE


def test_confirmed_claim_not_reused_by_new_run() -> None:
    cs = [claim("a", "10")]
    held = manual_match(rem("r1", "10"), [(cs[0], D("10"))])
    res = reconcile(
        [rem("r1", "10"), rem("r2", "10", invoice_no="INV-1", invoice_line=1)], cs, existing=[held]
    )
    assert res.matches == (held,)
    assert res.unmatched_remittance_ids == ("r2",)


def test_manual_and_confirm_suggestion() -> None:
    c1, c2 = claim("a", "10"), claim("b", "20", inv="INV-2")
    m = manual_match(rem("r", "30"), [(c1, D("10")), (c2, D("20"))])
    assert (m.method, m.status, m.auto_confirm) == (Method.MANUAL, Status.CONFIRMED, False)
    with pytest.raises(InvariantViolation):
        manual_match(rem("r", "25"), [(c1, D("10")), (c2, D("20"))])
    with pytest.raises(InvariantViolation):
        manual_match(rem("r", "10"), [(ClaimLine("z", "OTHER", "CL-z", "I", 1, D("10")), D("10"))])
    sug = reconcile([rem("r", "1000")], [claim("a", "400", inv="I-A"), claim("b", "600", inv="I-B")]).matches[
        0
    ]
    ok = confirm_suggestion(sug)
    assert ok.status is Status.CONFIRMED and ok.method is Method.SUBSET_SUM and not ok.auto_confirm


def test_overpayment_stays_visible_to_variance() -> None:
    res = reconcile([rem("r", "120", invoice_no="INV-1", invoice_line=1)], [claim("a", "100")])
    assert res.matches[0].allocations[0].amount == D("120")  # allocation = paid; §10.3 sees paid > claimed


def test_floats_rejected() -> None:
    with pytest.raises(TypeError):
        RemittanceLine("r", "M", 1.5)  # type: ignore[arg-type]
