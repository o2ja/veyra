"""Review counts for the extraction list (AI_PROCESSING.md §6): plain counts from stored data, no model call.

*Found* = a verified, high-confidence value with a quote and no validator error on it. Everything else the AI
read, plus every validator error that no extracted field accounts for, *needs the user's decision*.
"""

from typing import Any


def _hit(path: str, errors: set[str]) -> bool:
    return any(e == path or e.startswith(f"{path}.") for e in errors)


def counts(result: dict[str, Any] | None, validation: dict[str, Any] | None) -> tuple[int, int] | None:
    """``(found, needs_decision)``, or ``None`` when there is no result to count."""
    if not result or not isinstance(result.get("fields"), list):
        return None
    fields: list[dict[str, Any]] = result["fields"]
    errors = {str(e["field"]).removeprefix("payload.") for e in (validation or {}).get("errors", [])}
    found = sum(
        1
        for f in fields
        if f["confidence"] == "high"
        and f["value"] is not None
        and f["evidence"]
        and not (f.get("payload_path") and _hit(f["payload_path"], errors))
    )
    paths = [f["payload_path"] for f in fields if f.get("payload_path")]
    unexplained = sum(1 for e in errors if not any(e == p or e.startswith(f"{p}.") for p in paths))
    return found, len(fields) - found + unexplained
