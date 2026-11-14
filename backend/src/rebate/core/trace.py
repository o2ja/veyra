"""One node of a trace chain (API_SPECIFICATION.md §2.9): what the record is, where it comes from, as plain strings."""

from typing import Any
from uuid import UUID


def node(kind: str, ident: UUID | None, label: str, **fields: Any) -> dict[str, Any]:
    """``fields`` values are shown as text (money and dates keep their stored precision); ``None`` stays ``None``."""
    return {
        "kind": kind,
        "id": ident,
        "label": label,
        "fields": {k: None if v is None else str(v) for k, v in fields.items()},
    }


def merge(*chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Concatenate chains; a node (kind, id) appears once, at its first position."""
    seen: set[tuple[str, UUID | None]] = set()
    out: list[dict[str, Any]] = []
    for n in (n for chain in chains for n in chain):
        key = (n["kind"], n["id"])
        if n["id"] is not None and key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out
