"""S5.7 performance baseline: one run over 1,000,000 imported sales lines (deselected by default; run with
``-m performance`` and ``PERF_SALES_CSV`` pointing at a file made by ``testdata/generate.py --volume 1000000``).

Import goes through the real pipeline (see ``test_import_performance.py``); the numbers printed here are copied
into ``docs/reports/notes/c1-s5-runs.md``. Nothing is asserted about speed (machine dependent); the run must
complete and account for every line."""

import os
import resource
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from tests.integration.calc_support import activate_all, all_settings, open_workspace, run
from tests.integration.conftest import confirm, map_suggested, start
from tests.integration.test_import_performance import _rss_mb, _seed_clean_file
from tests.support import FakeClock

pytestmark = pytest.mark.performance


def test_one_million_lines_calculation(app: FastAPI, clk: FakeClock, admin_engine: Engine) -> None:
    source = os.environ.get("PERF_SALES_CSV")
    if not source:
        pytest.fail("set PERF_SALES_CSV to a file made by testdata/generate.py --volume 1000000 --out <dir>")
    path = Path(source)
    expected = sum(1 for _ in path.open("rb")) - 1
    with ExitStack() as stack:
        ws = open_workspace(app, stack)
        all_settings(ws)
        activate_all(ws, ("SPA-VOLT-2026-884",))
        batch = start(ws, _seed_clean_file(ws, admin_engine, path), "sales")
        if batch["needs_mapping"]:
            batch = map_suggested(ws, batch)
        done = confirm(ws, batch)
        assert done["status"] == "completed" and done["counts"]["inserted"] == expected, done
        rss0, t0 = _rss_mb(), time.perf_counter()
        r = run(ws, date_from="2000-01-01", date_to="2100-12-31")
        t1 = time.perf_counter()
        assert r["status"] == "completed", r
        assert r["totals"]["lines"] == expected
        print(
            f"\nPERF calc lines={expected} run={t1 - t0:.1f}s ({expected / (t1 - t0):.0f} lines/s) "
            f"peak_rss={_rss_mb():.0f}MB (before {rss0:.0f}MB, children "
            f"{resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024:.0f}MB) "
            f"eligible={r['totals']['by_status'].get('eligible', 0)}"
        )
