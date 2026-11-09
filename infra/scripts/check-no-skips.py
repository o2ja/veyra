"""Fail if any JUnit report contains skipped tests (TESTING_STRATEGY.md §1.6: skips need an owner)."""

import sys
import xml.etree.ElementTree as ET  # trusted input: reports produced by our own CI run

skipped: list[str] = []
total = 0
for path in sys.argv[1:]:
    for case in ET.parse(path).getroot().iter("testcase"):  # noqa: S314
        total += 1
        if case.find("skipped") is not None:
            skipped.append(f"{path}: {case.get('classname')}::{case.get('name')}")
if total == 0:
    sys.exit("no test cases found in reports")
if skipped:
    sys.exit("skipped tests are not allowed in CI:\n  " + "\n  ".join(skipped))
print(f"{total} test cases, none skipped")
