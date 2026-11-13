"""E2 end to end on SYNTHETIC data: master data + sales + purchases imported through the ingestion pipeline, all six
manual-payload agreements approved through the agreements service, calculation runs executed by the worker. Every
expected value comes from ``testdata/generated/ground_truth`` (``expected_calculation.json``, ``expected_rebates.json``,
``expected_rebates_nwnd.json``); a mismatch is a financial discrepancy, never a reason to edit this file."""

from collections.abc import Iterator
from contextlib import ExitStack
from decimal import Decimal
from typing import Any

import pytest

from tests.conftest import DbUrls
from tests.integration.calc_support import (
    activate_all,
    all_settings,
    claim_and_submit,
    import_sales,
    import_sales_part,
    open_workspace,
    results,
    run,
)
from tests.integration.conftest import Workspace, import_file
from tests.integration.test_calculation_runs import NOW, WINDOW, Env
from tests.support import fake_clock, make_app
from tests.testdata import truth

AGREEMENTS = (
    "SPA-VOLT-2026-884",
    "SPA-VOLT-2026-901",
    "SPA-BRIT-2026-031",
    "SPA-COPR-2026-107",
    "REB-VOLT-2026",
    "REB-NWND-2026",
)
CALC = truth("expected_calculation.json")
VOLT_LEDGER = truth("expected_rebates.json")
NWND = truth("expected_rebates_nwnd.json")


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


@pytest.fixture(scope="module")
def ws(env: Env) -> Workspace:
    w = open_workspace(env.app, env.stack)  # master data (customers, products, UoM) imported by the pipeline
    all_settings(w)
    activate_all(w, AGREEMENTS)
    import_sales(w)
    import_sales(w, NWND["file"])
    done = import_file(w, "purchases/purchases_VOLT_2026.csv", "purchases")
    assert done["status"] == "completed", done
    return w


def _accrual(ws: Workspace, run_id: str) -> dict[str, Any]:
    (entry,) = ws.api.get("/rebate-accruals", params={"run_id": run_id}).json()["data"]
    return dict(entry)


def test_volt_purchase_rebate_ledger_through_runs(ws: Workspace) -> None:
    """GL-R1a, GL-R1b, GL-R2 as three chained runs (as_of = run window end), then idempotent re-runs (delta 0.00)."""
    volt = ws.manufacturers["VOLT"]
    for case in VOLT_LEDGER["cases"]:
        r = run(ws, manufacturer_ids=[volt], date_from="2026-01-01", date_to=case["as_of"])
        assert r["status"] == "completed", (case["id"], r)
        e = _accrual(ws, r["id"])
        assert e["external_number"] == VOLT_LEDGER["agreement"], case["id"]
        assert e["as_of_date"] == case["as_of"], case["id"]
        assert Decimal(e["cumulative_qualifying"]) == Decimal(case["cumulative_qualifying"]), case["id"]
        assert e["tier_number"] == case["tier"], case["id"]
        assert Decimal(e["cumulative_earned"]) == Decimal(case["earned_cumulative"]), case["id"]
        assert Decimal(e["prior_cumulative_accrued"]) == Decimal(case["prior_accrued"]), case["id"]
        assert Decimal(e["delta"]) == Decimal(case["delta"]), case["id"]
        assert ("rebate_clawback" in e["flags"]) == (Decimal(case["delta"]) < 0), case["id"]
        again = run(ws, manufacturer_ids=[volt], date_from="2026-01-01", date_to=case["as_of"])
        assert Decimal(_accrual(ws, again["id"])["delta"]) == Decimal("0.00"), case["id"]  # idempotency


def test_nwnd_sales_rebate_qualify_only(ws: Workspace) -> None:
    (case,) = NWND["cases"]
    r = run(ws, manufacturer_ids=[ws.manufacturers["NWND"]], date_from="2026-01-01", date_to=case["as_of"])
    assert r["status"] == "completed", r
    e = _accrual(ws, r["id"])
    assert Decimal(e["cumulative_qualifying"]) == Decimal(case["cumulative_qualifying"])
    assert Decimal(e["cumulative_payout_base"]) == Decimal(case["payout_base"])
    assert e["tier_number"] == case["tier"]
    assert Decimal(e["cumulative_earned"]) == Decimal(case["earned_cumulative"])


def test_every_spa_scenario_matches_ground_truth(env: Env) -> None:
    """The seven lines of CB-VOLT-2026-06 are claimed and submitted through the real batch flow first (the ground
    truth's prior claims); the run that follows is the one checked against ``expected_calculation.json``. The
    ground truth's order of events is kept: the credit note CN-5002 arrives AFTER INV-10021/1 was claimed at 250.00
    (a return imported before the claim nets into it, 200.00: owner decision 2026-11-13, BUSINESS_LOGIC §7)."""
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    activate_all(ws, AGREEMENTS)
    import_sales_part(ws, late=False)
    fixture = truth("claim_batches_fixture.json")["lines"]
    batch = claim_and_submit(
        ws, results(ws, run(ws, **WINDOW)["id"]), [f"{x['invoice_no']}/{x['line_no']}" for x in fixture]
    )
    # pins the order of events: every line frozen at the fixture amount (INV-10021/1 at 250.00, no CN-5002 yet)
    frozen = ws.api.get(f"/claim-batches/{batch['id']}/lines", params={"limit": 200}).json()["data"]
    assert {x["document_number"]: Decimal(x["claim_amount"]) for x in frozen} == {
        x["invoice_no"]: Decimal(x["claim_amount"]) for x in fixture
    }
    import_sales_part(ws, late=True)
    r = run(ws, **WINDOW)
    assert r["status"] == "completed", r
    res = results(ws, r["id"])
    for sc in CALC["scenarios"]:
        got = res[f"{sc['invoice_no']}/{sc['line_no']}"]
        assert got["status"] == sc["status"], sc["id"]
        assert got["exception_code"] == sc.get("exception_code"), sc["id"]
        assert Decimal(got["claim_amount"]) == Decimal(sc.get("claim_amount", "0")), sc["id"]
        for key in ("claim_unit_amount", "eligible_quantity", "excess_quantity"):
            if key in sc:
                assert Decimal(got[key]) == Decimal(sc[key]), (sc["id"], key)
        assert set(sc.get("flags", [])) <= set(got["flags"]), sc["id"]
