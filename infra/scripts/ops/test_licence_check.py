"""Self-check for licence_check.classify. Run: python3 infra/scripts/ops/test_licence_check.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from licence_check import classify

CASES = {
    "MIT": "ok",
    "Apache-2.0 OR MIT": "ok",
    "MIT OR GPL-2.0": "ok",
    "GPL-3.0-only": "strong",
    "GPL-3.0-or-later": "strong",
    "GPLv3": "strong",
    "GPLv3+": "strong",
    "AGPL-3.0-or-later": "strong",
    "AGPLv3": "strong",
    "GNU General Public License v2 or later (GPLv2+)": "strong",
    "GNU General Public License v3 or later": "strong",
    "GPL-2.0 or later": "strong",
    "GPL-2.0-only OR GPL-3.0-only": "strong",
    "LGPL-2.1-or-later": "weak",
    "LGPLv3": "weak",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "weak",
    "MPL-2.0": "weak",
    "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License | GNU Affero General Public License v3 | Other/Proprietary License": "strong",
    "AGPL-3.0 or commercial": "unknown",
    "Commercial": "unknown",
    "UNLICENSED": "unknown",
    "SEE LICENSE IN LICENSE.txt": "unknown",
    "MIT | Other/Proprietary License": "unknown",
    "BUSL-1.1": "unknown",
    "Business Source License 1.1": "unknown",
    "Elastic License 2.0": "unknown",
    "Elastic-2.0": "unknown",
    "Commons Clause": "unknown",
    "CC-BY-NC-4.0": "unknown",
    "GNU Affero": "strong",
    "GPL-3.0-only OR LGPL-3.0": "weak",
}
bad = {k: (v, classify(k)) for k, v in CASES.items() if classify(k) != v}
assert not bad, f"(expected, got): {bad}"
print(f"licence_check: {len(CASES)} cases ok")
