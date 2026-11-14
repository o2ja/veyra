"""S5.7 / S11.2 performance baseline (deselected by default; select with ``-m performance``).

Scale is ``PERF_ROWS`` (default 100000; the owner decision D-N9 allows 100000 and 250000 on the dev laptop, never
1M). Generate the file first, from the repo root, into the git-ignored ``testdata/generated_volume/<rows>``::

    python3 -I testdata/generate.py --volume 250000 --out testdata/generated_volume/250000   # needs openpyxl, reportlab

and mount ``testdata`` at ``/testdata``. ``PERF_SALES_CSV`` overrides the file path. Import goes through the real
pipeline (see ``test_import_performance.py``). The clock is pinned to the generator's ``run_date`` (2026-07-10) so the
June invoices are inside the claim window; the eligible count and amount asserted come from the generator's ground
truth (``ground_truth/expected_volume_summary.json``), never from the code under test. Speed is not asserted
(machine dependent); the figures printed (``PERF ...``) go into ``docs/reports/PERFORMANCE_BASELINES.md``. After the
run the three hottest list queries (sales lines, run results, missed claims) are captured from real API calls and
re-run under ``EXPLAIN (ANALYZE, BUFFERS)``."""

import json
import os
import re
import resource
import threading
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event

from tests.integration.calc_support import activate_all, all_settings, open_workspace, run
from tests.integration.conftest import confirm, map_suggested, start
from tests.integration.test_import_performance import _rss_mb, _seed_clean_file
from tests.support import FakeClock

pytestmark = pytest.mark.performance

ROWS = int(os.environ.get("PERF_ROWS", "100000"))


def _rss_now_mb() -> float:
    with open("/proc/self/status") as f:  # Linux; current (not peak) resident set
        return int(re.search(r"VmRSS:\s+(\d+)", f.read()).group(1)) / 1024  # type: ignore[union-attr]


class _RssSampler:
    """Peak current RSS over a phase (ru_maxrss is process-lifetime, so it cannot isolate the import)."""

    def __enter__(self) -> _RssSampler:
        self.peak, self._stop = _rss_now_mb(), threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(0.25):
            self.peak = max(self.peak, _rss_now_mb())

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._t.join()
        self.peak = max(self.peak, _rss_now_mb())


def _explain(admin: Engine, label: str, statements: list[tuple[str, object]]) -> None:
    seen: set[str] = set()
    for sql, params in statements:
        if sql in seen or not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            continue
        seen.add(sql)
        try:
            with admin.connect() as c:
                plan = "\n".join(r[0] for r in c.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + sql, params))
        except Exception as e:  # report, never fail the baseline on a plan capture
            plan = f"(could not explain: {type(e).__name__}: {str(e)[:200]})"
        print(f"\nPERF EXPLAIN {label}\nSQL: {' '.join(sql.split())[:600]}\n{plan}")


def test_calculation_performance(app: FastAPI, clk: FakeClock, admin_engine: Engine) -> None:
    path = Path(
        os.environ.get("PERF_SALES_CSV") or f"/testdata/generated_volume/{ROWS}/sales/sales_volume_{ROWS}.csv"
    )
    if not path.exists():
        pytest.fail(f"{path} missing: generate it (see the module docstring)")
    expected = sum(1 for _ in path.open("rb")) - 1
    gt = json.loads((path.parent.parent / "ground_truth" / "expected_volume_summary.json").read_text())
    assert gt["file"] == path.name and gt["run_date"] == "2026-07-10", gt
    # pinned before everything: the run's data cutoff and agreement visibility are both "now", so setup and import
    # must be stamped no later than the run. 2026-07-10 is the generator run_date: June invoices are in the window.
    clk.t = datetime(2026, 7, 10, 12, tzinfo=UTC)
    with ExitStack() as stack:
        ws = open_workspace(app, stack)
        all_settings(ws)
        activate_all(ws, ("SPA-VOLT-2026-884",))
        ti = time.perf_counter()
        with _RssSampler() as imp:
            batch = start(ws, _seed_clean_file(ws, admin_engine, path), "sales")
            if batch["needs_mapping"]:
                batch = map_suggested(ws, batch)
            done = confirm(ws, batch)
        print(
            f"\nPERF import rows={expected} {time.perf_counter() - ti:.1f}s peak_rss_during_import={imp.peak:.0f}MB "
            f"ru_maxrss={_rss_mb():.0f}MB"
        )
        assert done["status"] == "completed" and done["counts"]["inserted"] == expected, done
        t0 = time.perf_counter()
        with _RssSampler() as calc:
            r = run(ws, date_from="2000-01-01", date_to="2100-12-31")
        t1 = time.perf_counter()
        assert r["status"] == "completed", r
        by = r["totals"]["by_status"]
        print(
            f"\nPERF calc lines={expected} run={t1 - t0:.1f}s ({expected / (t1 - t0):.0f} lines/s) "
            f"peak_rss_during_run={calc.peak:.0f}MB ru_maxrss={_rss_mb():.0f}MB (children "
            f"{resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024:.0f}MB) by_status={by} "
            f"by_exception={r['totals']['by_exception']} eligible_amount={r['totals']['eligible_amount']}"
        )
        assert r["totals"]["lines"] == expected
        assert by.get("eligible", 0) == gt["eligible_count"] > 0
        assert r["totals"]["by_exception"] == gt["exceptions"] | {
            k: v for k, v in r["totals"]["by_exception"].items() if k not in gt["exceptions"]
        }
        assert Decimal(r["totals"]["eligible_amount"]) == Decimal(gt["eligible_total"])

        # the three hottest list endpoints, captured from real calls then explained
        captured: list[tuple[str, object]] = []

        def grab(conn, cur, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
            captured.append((statement, parameters))

        event.listen(Engine, "before_cursor_execute", grab)
        try:
            for label, url, needle in (
                ("lines list", "/sales-lines?limit=50", "sales_document_lines"),
                (
                    "results list",
                    f"/calculation-runs/{r['id']}/results?limit=50&status=eligible",
                    "calculation_results",
                ),
                ("missed claims", "/missed-claims?limit=50", "calculation_results"),
            ):
                captured.clear()
                tq = time.perf_counter()
                resp = ws.api.get(url)
                dt = time.perf_counter() - tq
                assert resp.status_code == 200, resp.text
                print(
                    f"\nPERF endpoint {label} {url.split('?')[0].split('/')[1]} {dt * 1000:.0f}ms rows={len(resp.json()['data'])}"
                )
                _explain(admin_engine, label, [(s, p) for s, p in captured if needle in s])
        finally:
            event.remove(Engine, "before_cursor_execute", grab)
