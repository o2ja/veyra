"""Isolation negative control (kickoff report §6.4, CLAUDE.md §11 exception, S1 D-1).

Runs tests/isolation against the throwaway database `rebate_negctl` with RLS switched off and checks:

* every RLS-dependent test FAILS, and fails with an ISOLATION-BREACH assertion (not some other error);
* every other isolation test (grants, SECURITY DEFINER shape, route tests: application layer) PASSES.

Then drops `rebate_negctl`. Exit code 0 only if both hold. Usage (inside the api container):

    TEST_ADMIN_DATABASE_URL=... python -m tests.isolation.negative_control
"""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy import create_engine, make_url, text

BACKEND = Path(__file__).resolve().parents[2]
BREACH = "ISOLATION-BREACH"

MIN_TESTS = 64  # tests/isolation size at S2; raise it as the suite grows

# Tests whose protection comes from RLS. They must fail with a BREACH assertion once RLS is off.
RLS_DEPENDENT = (
    "test_d1_",
    "test_d2_",
    "test_d3_",
    "test_d4_",
    "test_d5_",
    "test_d6_",
    "test_d7_",
    "test_d8_",
    "test_d9_row_security_off",
    "test_d12_",
    "test_d13_idempotency",
    "test_p1_",
    "test_p2_",
    "test_p3_",
)


def _classify(junit: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    report: list[str] = []
    cases = ET.parse(junit).getroot().iter("testcase")  # noqa: S314
    for case in cases:
        name = case.get("name", "")
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        dependent = name.startswith(RLS_DEPENDENT)
        if skipped is not None or error is not None:
            problems.append(f"{name}: skipped/errored (must pass or fail by assertion)")
            outcome = "ERROR"
        elif failure is not None:
            text_ = (failure.get("message") or "") + (failure.text or "")
            breached = BREACH in text_
            outcome = "FAILED (breach detected)" if breached else "FAILED (other reason)"
            if not dependent:
                problems.append(f"{name}: failed without RLS but should not depend on RLS")
            elif not breached:
                problems.append(f"{name}: failed, but not with an {BREACH} assertion")
        else:
            outcome = "passed"
            if dependent:
                problems.append(f"{name}: PASSED with RLS off, so it does not test RLS")
        report.append(f"{'RLS-dependent' if dependent else 'independent  '} | {outcome:26} | {name}")
    # A truncated or empty run must not pass vacuously (S1 code review finding 10).
    names = [line.rsplit("| ", 1)[-1] for line in report]
    problems += [
        f"no test matched RLS-dependent prefix {p!r}"
        for p in RLS_DEPENDENT
        if not any(x.startswith(p) for x in names)
    ]
    if len(report) < MIN_TESTS:
        problems.append(f"only {len(report)} isolation tests ran (expected at least {MIN_TESTS})")
    return problems, report


def main() -> int:
    env = dict(os.environ, REBATE_TEST_DB="rebate_negctl", REBATE_NEGCTL="1")
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "negctl.xml"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--no-cov",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={junit}",
                "tests/isolation",
            ],
            cwd=BACKEND,
            env=env,
            check=False,
        )
        problems, report = _classify(junit)
    server = create_engine(
        make_url(os.environ["TEST_ADMIN_DATABASE_URL"]).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with server.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS rebate_negctl WITH (FORCE)"))
    server.dispose()

    print("\nNegative control: tests/isolation with RLS DISABLED on every tenant table")
    print("\n".join(sorted(report)))
    if problems:
        print("\nNEGATIVE CONTROL FAILED:\n  " + "\n  ".join(problems))
        return 1
    print(
        f"\nNEGATIVE CONTROL OK: {sum(r.startswith('RLS') for r in report)} RLS-dependent tests detected the "
        "breach; all independent tests still passed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
