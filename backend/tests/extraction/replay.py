"""Transport-level replay of recorded provider responses (OD-2: no network, no key, no live call).

The real ``AnthropicProvider`` and Anthropic SDK run unchanged; only the HTTP transport underneath is
replaced, so request building, retry mapping, tool-use parsing and usage accounting are all exercised.
"""

import json
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2

RECORDED = Path(__file__).parent / "fixtures" / "recorded"
# marker printed in the document -> fixture stem
MARKERS = {
    "SPA-VOLT-2026-884": "SYNTHETIC_spa_voltara_SPA-VOLT-2026-884",
    "SPA-VOLT-2026-950": "SYNTHETIC_spa_injection_test_SPA-VOLT-2026-950",
    "SPA-BRIT-2026-077": "SYNTHETIC_spa_missing_info_SPA-BRIT-2026-077",
    "SPA-COPR-2026-220": "SYNTHETIC_spa_coppermark_multipage_SPA-COPR-2026-220",
    "SPA-BRIT-2026-031": "SYNTHETIC_spa_brightline_items",
    "REB-VOLT-2026": "SYNTHETIC_rebate_voltara_REB-VOLT-2026",
    "REB-NWND-2026": "SYNTHETIC_rebate_northwind_REB-NWND-2026",
}

Step = Callable[[dict[str, Any]], httpx2.Response]


def message(
    tool_input: dict[str, Any], *, tokens: tuple[int, int] = (1000, 200), tool: str = "record_extraction"
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "tool_use", "id": "toolu_test", "name": tool, "input": tool_input}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
    }


def error(status: int, headers: dict[str, str] | None = None, message: str = "scripted failure") -> Step:
    body = {"type": "error", "error": {"type": "api_error", "message": message}}
    return lambda _b: httpx2.Response(status, json=body, headers=headers)


def ok(tool_input: dict[str, Any], **kw: Any) -> Step:
    return lambda _b: httpx2.Response(200, json=message(tool_input, **kw))


def timeout(_b: dict[str, Any]) -> httpx2.Response:
    raise httpx2.ReadTimeout("scripted timeout")


class Replay:
    """Scripted steps first (failure injection), then the recorded fixture for the document in the prompt."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script: deque[Step] = deque()

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if self.script:
            return self.script.popleft()(body)
        return self.recorded(body)

    @staticmethod
    def recorded(body: dict[str, Any]) -> httpx2.Response:
        user = body["messages"][0]["content"]
        variant = "b" if "Work in this order" in body["system"] else "a"
        stem = next((s for m, s in MARKERS.items() if m in user), None)
        if stem is None:
            return httpx2.Response(
                500, json={"type": "error", "error": {"type": "api_error", "message": "no fixture"}}
            )
        return httpx2.Response(
            200, json=json.loads((RECORDED / f"{stem}.{variant}.json").read_text(encoding="utf-8"))
        )
