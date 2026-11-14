"""Engine vs testdata/generated/ground_truth (TEST_DATA_SPEC.md §3), pure engine with in-memory inputs.

Expected values are read from the ground-truth files; inputs are built from the SYNTHETIC CSVs and
manual payloads. The DB end-to-end variant of these scenarios is S5.7 (orchestration), not here.
"""

from collections import Counter
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from rebate_engine.models import Code, LineResult, PriorClaim, SpaRunInput, SpaVersion, Status
from rebate_engine.rebate import CLAWBACK, calculate_accrual
from rebate_engine.spa import CANNOT_BE_NET_CLAIMED, calculate_spa, propose_recalculation
from tests.engine_golden import testdata_inputs as td

CALC = td.ground_truth("expected_calculation.json")
RUN_DATE = date.fromisoformat(CALC["run_date"])
SCENARIO_CSV = "sales/sales_scenarios_layoutA.csv"


def _claimed_batch() -> dict[str, PriorClaim]:
    """claim_batches_fixture.json: the submitted batch CB-VOLT-2026-06 (precondition of SC-09r)."""
    batch = td.ground_truth("claim_batches_fixture.json")
    by_line = {f"{s['invoice_no']}/{s['line_no']}": s for s in CALC["scenarios"]}
    rounding = td.spa_version("SPA-VOLT-2026-884").rules.rounding
    out = {}
    for ln in batch["lines"]:
        lid = f"{ln['invoice_no']}/{ln['line_no']}"
        out[lid] = PriorClaim(
            agreement_id="SPA-VOLT-2026-884",
            agreement_version_id="SPA-VOLT-2026-884",
            claim_uom="EA",
            quantity=Decimal(by_line[lid]["eligible_quantity"]),
            unit_amount=Decimal(by_line[lid]["claim_unit_amount"]),
            claim_amount=Decimal(ln["claim_amount"]),
            rounding=rounding,
        )
    return out


def _run(versions: tuple[SpaVersion, ...] | None = None, **mfr: dict[str, Any]) -> dict[str, LineResult]:
    run = SpaRunInput(
        run_date=RUN_DATE,
        lines=td.sales_lines(SCENARIO_CSV, _claimed_batch()),
        versions=versions or tuple(td.spa_version(n) for n in td.SPA_PAYLOADS),
        manufacturers=td.manufacturers(**mfr),
        conversions=td.conversions(),
    )
    return {r.line_id: r for r in calculate_spa(run)}


def _step(result: LineResult, name: str) -> dict[str, Any]:
    steps = result.trace["steps"]
    assert isinstance(steps, list)
    found = [s for s in steps if isinstance(s, dict) and s.get("step") == name]
    assert found, f"{result.line_id}: no {name} step"
    step = found[-1]
    assert isinstance(step, dict)
    return step


def _assert_scenario(sc: dict[str, Any], r: LineResult) -> None:
    assert r.status == Status(sc["status"]), sc["id"]
    assert r.exception_code == (Code(sc["exception_code"]) if "exception_code" in sc else None), sc["id"]
    # no stored amount unless the doc says so (outside_claim_window keeps the money at risk, §4.0 gate 9)
    assert r.claim_amount == Decimal(sc.get("claim_amount", "0")), sc["id"]
    for key, attr in (
        ("claim_unit_amount", "claim_unit_amount"),
        ("eligible_quantity", "eligible_quantity"),
        ("excess_quantity", "excess_quantity"),
    ):
        if key in sc:
            assert getattr(r, attr) == Decimal(sc[key]), (sc["id"], key)
    assert set(sc.get("flags", [])) <= set(r.flags), sc["id"]
    result = r.trace["result"]
    assert isinstance(result, dict)
    assert result["status"] == sc["status"]
    assert result["exception"] == sc.get("exception_code")
    if sc["status"] == "eligible":
        assert Decimal(str(_step(r, "calc")["unit_amount"])) == Decimal(sc["claim_unit_amount"])
        assert Decimal(str(_step(r, "round")["result"])) == Decimal(sc["claim_amount"])


@pytest.mark.parametrize("sc", CALC["scenarios"], ids=lambda s: s["id"])
def test_scenario(sc: dict[str, Any]) -> None:
    results = _run()
    _assert_scenario(sc, results[f"{sc['invoice_no']}/{sc['line_no']}"])


def test_every_scenario_line_is_covered_by_ground_truth() -> None:
    expected = {f"{s['invoice_no']}/{s['line_no']}" for s in CALC["scenarios"]}
    assert set(_run()) == expected


def test_return_after_claim_is_flagged_when_negative_claims_not_allowed() -> None:
    # SC-09r: VOLT accepts negative claims in the ground truth (master_manufacturers.json): -50.00, no flag
    r = _run()["CN-5002/1"]
    assert r.status is Status.ADJUSTMENT and r.claim_amount == Decimal("-50.00")
    assert CANNOT_BE_NET_CLAIMED not in r.flags
    assert r.agreement_version_id == "SPA-VOLT-2026-884"
    # a manufacturer without the setting: same amount, flagged "cannot be net-claimed" (BUSINESS_LOGIC §7)
    blocked = _run(VOLT={"allow_negative_claims": False})["CN-5002/1"]
    assert blocked.claim_amount == Decimal("-50.00") and CANNOT_BE_NET_CLAIMED in blocked.flags


def _variant(vid: str) -> dict[str, Any]:
    return next(v for v in CALC["variants"] if v["id"] == vid)


def _with_everg_in_884(priority_901: int = 0) -> tuple[SpaVersion, ...]:
    p884 = td.payload("SPA-VOLT-2026-884")
    p884["customers"].append({"customer_code": "EVERG", "scope": "include"})
    p901 = td.payload("SPA-VOLT-2026-901")
    p901["header"]["priority"] = priority_901
    return (
        td.spa_version("SPA-BRIT-2026-031"),
        td.spa_version("SPA-COPR-2026-107"),
        td.spa_version("SPA-VOLT-2026-884", p884),
        td.spa_version("SPA-VOLT-2026-901", p901),
    )


def test_variant_sc14b_conflict_flag() -> None:
    v = _variant("SC-14b")
    r = _run(_with_everg_in_884())["INV-10034/1"]
    assert r.status == Status(v["status"]) and r.exception_code == Code(v["exception_code"])
    assert r.claim_amount == Decimal(v["claim_amount"])
    conflict = _step(r, "conflict")
    assert conflict["winner"] is None and len(conflict["candidates"]) == 2


def test_variant_sc14c_priority_winner() -> None:
    v = _variant("SC-14c")
    r = _run(_with_everg_in_884(priority_901=10), VOLT={"conflict_policy": "priority"})["INV-10034/1"]
    assert r.status == Status(v["status"]) and r.claim_amount == Decimal(v["claim_amount"])
    assert r.agreement_version_id == v["winner"]


def test_variant_amendment_split_supersede_leaves_june_lines_unchanged() -> None:
    """SC-07-roundtrip / GL-A1: 884 split at 2026-07-01 (v1a identical terms, v2 deviated 80.00)."""
    base = _run()
    p = td.payload("SPA-VOLT-2026-884")
    v1a = replace(td.spa_version("SPA-VOLT-2026-884"), version_id="884-v1a", valid_to=date(2026, 6, 30))
    p["header"]["valid_from"] = "2026-07-01"
    for item in p["items"]:
        if item["sku"] == "BRK-3P-100A":
            item["deviated_cost"] = "80.00"
    v2 = replace(td.spa_version("SPA-VOLT-2026-884", p), version_id="884-v2")
    others = tuple(td.spa_version(n) for n in td.SPA_PAYLOADS if n != "SPA-VOLT-2026-884")
    after = _run((*others, v1a, v2))
    batch = _claimed_batch()
    for lid, frozen in batch.items():
        assert after[lid].agreement_version_id == "884-v1a", lid
        assert after[lid].claim_amount == base[lid].claim_amount, lid
        assert propose_recalculation(frozen.claim_amount, after[lid]) is None, lid


def test_volume_summary_2000_lines() -> None:
    gt = td.ground_truth("expected_volume_summary.json")
    run = SpaRunInput(
        run_date=date.fromisoformat(gt["run_date"]),
        lines=td.sales_lines(f"sales/{gt['file']}"),
        versions=(td.spa_version("SPA-VOLT-2026-884"),),
        manufacturers=td.manufacturers(),
        conversions=td.conversions(),
    )
    results = calculate_spa(run)
    eligible = [r for r in results if r.status is Status.ELIGIBLE]
    assert len(eligible) == gt["eligible_count"]
    assert sum((r.claim_amount for r in eligible), Decimal(0)) == Decimal(gt["eligible_total"])
    exceptions = Counter(r.exception_code.value for r in results if r.exception_code is not None)
    assert dict(exceptions) == gt["exceptions"]


def test_volt_purchase_rebate_ledger() -> None:
    """expected_rebates.json: GL-R1a/R1b/R2 chained through the append-only ledger, then idempotent reruns."""
    gt = td.ground_truth("expected_rebates.json")
    version = td.rebate_version(gt["agreement"])
    lines = td.purchase_volume_lines()
    ledger = Decimal(0)
    for case in gt["cases"]:
        assert ledger == Decimal(case["prior_accrued"]), case["id"]
        r = calculate_accrual(version, lines, date.fromisoformat(case["as_of"]), ledger)
        assert r.qualifying_volume == Decimal(case["cumulative_qualifying"]), case["id"]
        assert r.tier_number == case["tier"], case["id"]
        assert r.earned_cumulative == Decimal(case["earned_cumulative"]), case["id"]
        assert r.delta == Decimal(case["delta"]), case["id"]
        assert (CLAWBACK in r.flags) == (r.delta < 0), case["id"]
        ledger += r.delta
        again = calculate_accrual(version, lines, r.as_of, ledger)
        assert again.delta == Decimal("0.00"), case["id"]  # gt["idempotency"]


def test_nwnd_sales_rebate_qualify_only() -> None:
    gt = td.ground_truth("expected_rebates_nwnd.json")
    (case,) = gt["cases"]
    r = calculate_accrual(
        td.rebate_version(gt["agreement"]),
        td.sales_volume_lines(gt["file"]),
        date.fromisoformat(case["as_of"]),
        Decimal(0),
    )
    assert r.qualifying_volume == Decimal(case["cumulative_qualifying"])
    assert r.payout_volume == Decimal(case["payout_base"])
    assert r.tier_number == case["tier"]
    assert r.earned_cumulative == Decimal(case["earned_cumulative"])


# ---- return before claim, re-run after the claim (owner decision 2026-11-13, D-N1) -------------------------------


def _claimed_net_of_cn5002() -> dict[str, PriorClaim]:
    """INV-10021/1 claimed AFTER CN-5002 (4 units) was netted: hand 20 - 4 = 16 units x 12.50 = 200.00 frozen."""
    prior = _claimed_batch()
    prior["INV-10021/1"] = replace(
        prior["INV-10021/1"],
        quantity=Decimal("16"),
        claim_amount=Decimal("200.00"),
        netted_returned_quantity=Decimal("4"),
    )
    return prior


def _run_with(prior: dict[str, PriorClaim], extra_return_qty: Decimal | None = None) -> dict[str, LineResult]:
    lines = list(td.sales_lines(SCENARIO_CSV, prior))
    if extra_return_qty is not None:
        # a second return of INV-10021/1 imported after the claim: CN-5002 (4) comes first in processing order
        orig = next(ln for ln in lines if ln.line_id == "INV-10021/1")
        cn = next(ln for ln in lines if ln.line_id == "CN-5002/1")
        assert cn.return_original is not None
        total = Decimal("4") + extra_return_qty
        later = replace(
            cn,
            line_id="CN-5003/1",
            document_number="CN-5003",
            invoice_date=date(2026, 7, 3),
            quantity=-extra_return_qty,
            return_original=replace(
                cn.return_original, returned_quantity=total, returned_before=Decimal("4")
            ),
        )
        lines = [
            replace(ln, returned_quantity=total) if ln is orig else ln for ln in lines if ln is not cn
        ] + [replace(cn, return_original=replace(cn.return_original, returned_quantity=total)), later]
    run = SpaRunInput(
        run_date=RUN_DATE,
        lines=tuple(lines),
        versions=tuple(td.spa_version(n) for n in td.SPA_PAYLOADS),
        manufacturers=td.manufacturers(),
        conversions=td.conversions(),
    )
    return {r.line_id: r for r in calculate_spa(run)}


def test_return_before_claim_rerun_after_claim_proposes_nothing() -> None:
    res = _run_with(_claimed_net_of_cn5002())
    # original re-evaluated on the netting basis of its claim: 16 x 12.50 = 200.00 = the frozen amount (no 250.00)
    assert res["INV-10021/1"].claim_amount == Decimal("200.00")
    assert propose_recalculation(Decimal("200.00"), res["INV-10021/1"]) is None
    # the return is already netted into the claim: no adjustment, no amount
    assert res["CN-5002/1"].status is Status.NETTED and res["CN-5002/1"].claim_amount == Decimal("0")


def test_second_return_after_the_claim_is_reversed_at_the_frozen_unit_amount() -> None:
    # hand: 3 units x frozen unit amount 12.50 = -37.50; CN-5002 (4 units) remains netted
    res = _run_with(_claimed_net_of_cn5002(), extra_return_qty=Decimal("3"))
    assert res["INV-10021/1"].claim_amount == Decimal("200.00")
    assert res["CN-5002/1"].status is Status.NETTED
    assert res["CN-5003/1"].status is Status.ADJUSTMENT and res["CN-5003/1"].claim_amount == Decimal("-37.50")
