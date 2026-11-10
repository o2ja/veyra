"""Locate the SYNTHETIC test data: ``/testdata`` in the dev containers, ``../testdata`` in CI."""

import json
import os
from pathlib import Path
from typing import Any

_CANDIDATES = (
    os.environ.get("TESTDATA_DIR"),
    "/testdata",
    str(Path(__file__).resolve().parents[2] / "testdata"),
)
ROOT = next(Path(p) for p in _CANDIDATES if p and (Path(p) / "generated").is_dir())
GENERATED = ROOT / "generated"


def data(rel: str) -> bytes:
    return (GENERATED / rel).read_bytes()


def truth(name: str) -> Any:
    return json.loads((GENERATED / "ground_truth" / name).read_text(encoding="utf-8"))
