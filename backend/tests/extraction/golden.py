"""Paths to the SYNTHETIC golden set (repo checkout in CI; mounted at /testdata in the dev container)."""

import json
from pathlib import Path
from typing import Any

_CANDIDATES = (Path(__file__).resolve().parents[3] / "testdata", Path("/testdata"))
TESTDATA = next((p for p in _CANDIDATES if (p / "generated").is_dir()), _CANDIDATES[0])
AGREEMENTS = TESTDATA / "generated" / "agreements"
EXPECTED = TESTDATA / "generated" / "ground_truth" / "expected_extraction"


def ground_truth(stem: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXPECTED / f"{stem}.json").read_text(encoding="utf-8"))
    return data


def document_bytes(name: str) -> bytes:
    return (AGREEMENTS / name).read_bytes()
