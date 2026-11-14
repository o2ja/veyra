"""Trace builder and canonical hashing (BUSINESS_LOGIC.md §13).

All numbers in a trace are strings; steps keep insertion order; the canonical form sorts keys.
"""

import hashlib
import json
from datetime import date
from decimal import Decimal

from rebate_engine.models import Json

ENGINE_VERSION = "1.1.0"

type Value = Decimal | date | str | int | bool | list[Value] | tuple[Value, ...] | dict[str, Value] | None


def j(value: Value) -> Json:
    """Convert an engine value to its trace form (Decimal/date -> string)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [j(v) for v in value]
    if isinstance(value, dict):
        return {k: j(v) for k, v in value.items()}
    return value


class Trace:
    def __init__(self, subject_key: str, subject: dict[str, Value]) -> None:
        self._subject_key = subject_key
        self._subject = j(subject)
        self.steps: list[Json] = []
        self.warnings: list[Json] = []

    def step(self, name: str, **values: Value) -> None:
        self.steps.append({"step": name, **{k: j(v) for k, v in values.items()}})

    def warn(self, code: str) -> None:
        self.warnings.append(code)

    def build(self, rules_schema_version: int | None, result: dict[str, Value]) -> dict[str, Json]:
        out: dict[str, Json] = {
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": rules_schema_version,
            self._subject_key: self._subject,
            "steps": self.steps,
        }
        if self.warnings:
            out["warnings"] = self.warnings
        out["result"] = j(result)
        return out


def canonical_json(trace: Json) -> str:
    """Byte-stable form: sorted keys, no whitespace, UTF-8."""
    return json.dumps(trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def trace_hash(trace: Json) -> str:
    return hashlib.sha256(canonical_json(trace).encode("utf-8")).hexdigest()
