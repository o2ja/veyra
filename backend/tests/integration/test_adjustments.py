"""S7.4 against real Postgres, real imports, runs, batches and claim lines (no stand-ins).

Expected values come from ``testdata/generated/ground_truth`` (``expected_calculation.json``,
``claim_batches_fixture.json``, ``expected_changed_rows.json``) or from the hand derivations in the docstrings."""

from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI

from tests.conftest import DbUrls
from tests.integration.calc_support import (
    activate_all,
    all_settings,
    claim_and_submit,
    import_sales_part,
    open_workspace,
    results,
    run,
)
from tests.integration.conftest import Workspace
from tests.support import FakeClock, fake_clock, make_app
from tests.testdata import truth

NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
SPA_NAMES = ("SPA-VOLT-2026-884", "SPA-VOLT-2026-901", "SPA-BRIT-2026-031", "SPA-COPR-2026-107")
WINDOW = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
CALC = {f"{s['invoice_no']}/{s['line_no']}": s for s in truth("expected_calculation.json")["scenarios"]}
FIXTURE = truth("claim_batches_fixture.json")
FIX7 = [f"{x['invoice_no']}/{x['line_no']}" for x in FIXTURE["lines"]]


@dataclass
class Env:
    app: FastAPI
    stack: ExitStack
    clock: FakeClock


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc)


def workspace(env: Env) -> Workspace:
    ws = open_workspace(env.app, env.stack)
    all_settings(ws)
    activate_all(ws, SPA_NAMES)
    return ws


# ---- S7.4 step 1: the claim_lines reader --------------------------------------------------------------------


def test_cap_consumption_of_a_submitted_claim_survives_a_narrower_run_scope(env: Env) -> None:
    """BUSINESS_LOGIC §4.2 / GL-S6: PNL-200A is capped at 100 units. INV-10010/1 (60 units, 2026-05-12) is claimed
    and submitted: 60 x 52.00 = 3,120.00 (ground truth SC-07a). A later run whose date scope excludes it sees B =
    INV-10011/1 (60 units, 2026-05-20): only 100 - 60 = 40 units are left -> 40 x 52.00 = 2,080.00 (SC-07b), not
    the 60 x 52.00 = 3,120.00 a run that has forgotten the claim would give."""
    ws = workspace(env)
    import_sales_part(ws, late=False)
    first = run(ws, **WINDOW)
    a = results(ws, first["id"])
    assert Decimal(a["INV-10010/1"]["claim_amount"]) == Decimal("3120.00")
    claim_and_submit(ws, a, ["INV-10010/1"])
    later = run(ws, manufacturer_ids=[], date_from="2026-05-13", date_to="2026-05-31")
    assert later["status"] == "completed", later
    b = results(ws, later["id"])["INV-10011/1"]
    assert b["status"] == "eligible" and Decimal(b["eligible_quantity"]) == Decimal("40")
    assert Decimal(b["claim_amount"]) == Decimal("2080.00")
    assert "INV-10010/1" not in results(ws, later["id"])  # the claimed line is outside this run's scope
