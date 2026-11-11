"""Author the recorded-response fixtures (run by hand: ``python -m tests.extraction.record_fixtures``).

OD-2 is unresolved, so there is no live recording. Each fixture is a Messages-API response body of the shape
the provider returns (one forced ``record_extraction`` tool call + usage), whose tool input is what a
**correct model** would answer for the synthetic document: values from ``expected_extraction/*.json``, quotes
hand-copied from the PDF text (the builders in ``test_golden``). Pass B is written the other way round (rates
as printed percentages) so the percent normalisation is exercised. A later live run replaces them.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.extraction.test_golden import CASES

OUT = Path(__file__).parent / "fixtures" / "recorded"
USAGE = {"a": (4200, 1100), "b": (4300, 1150)}  # authored token counts, fixed so cost assertions are exact


def percent_rates(built: dict[str, Any]) -> dict[str, Any]:
    for row in built.get("tiers", []):
        v = row["rate"]["value"]
        if v is not None:
            row["rate"]["value"] = format(Decimal(v) * 100, "f").rstrip("0").rstrip(".") + "%"
    return built


def message(variant: str, built: dict[str, Any]) -> dict[str, Any]:
    tokens_in, tokens_out = USAGE[variant]
    return {
        "id": f"msg_recorded_{variant}",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [
            {"type": "tool_use", "id": f"toolu_{variant}", "name": "record_extraction", "input": built}
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stem, (_, build) in CASES.items():
        for variant, built in (("a", build()), ("b", percent_rates(build()))):
            (OUT / f"{stem}.{variant}.json").write_text(
                json.dumps(message(variant, built), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    main()
