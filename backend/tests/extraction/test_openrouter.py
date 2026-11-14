"""OpenRouterProvider: forced tool call over OpenAI-compatible chat completions (recorded/fake HTTP only)."""

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from rebate.core.config import Settings
from rebate.modules.extraction import prompts
from rebate.modules.extraction.gateway import (
    NotConfigured,
    OpenRouterProvider,
    ProviderCallError,
    ProviderResponse,
    _inline_refs,
    make_provider,
)

KEY = "test-openrouter-secret-key-12345"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"manufacturer": {"type": "string"}, "rebate_percent": {"type": "number"}},
    "required": ["manufacturer", "rebate_percent"],
}
DATA = {"manufacturer": "Voltara Electric", "rebate_percent": 2.5}


@pytest.fixture(autouse=True)
def _no_ambient_ai_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev stack puts real AI settings in the container environment; these tests must not see them."""
    for name in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_DATA_COLLECTION",
    ):
        monkeypatch.delenv(name, raising=False)


def _reply(
    args: Any = None, *, finish: str = "tool_calls", calls: bool = True, tin: int = 120, tout: int = 30
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": None}
    if calls:
        message["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    "name": "record",
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
        ]
    return {
        "choices": [{"finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout},
    }


def _provider(handler: Any, data_collection: str = "deny") -> OpenRouterProvider:
    return OpenRouterProvider(KEY, data_collection, httpx.Client(transport=httpx.MockTransport(handler)))


def _call(p: OpenRouterProvider, schema: dict[str, Any] = SCHEMA) -> ProviderResponse:
    return p.complete(
        model="vendor/model:free",
        system="sys",
        user="usr",
        tool_name="record",
        schema=schema,
        max_tokens=256,
        timeout=5,
    )


def test_success_parses_the_forced_tool_call_and_sends_the_right_request() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["req"], seen["body"] = req, json.loads(req.content)
        return httpx.Response(200, json=_reply(DATA))

    r = _call(_provider(handler))
    assert (r.data, r.input_tokens, r.output_tokens) == (DATA, 120, 30)
    req, body = seen["req"], seen["body"]
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(req.url) and KEY.encode() not in req.content
    assert body["tool_choice"] == {"type": "function", "function": {"name": "record"}}
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}
    assert body["stream"] is False and body["tools"][0]["function"]["parameters"] == SCHEMA
    assert "referer" not in {h.lower() for h in req.headers}


def test_data_collection_follows_the_setting() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content)["provider"]["data_collection"] == "allow"
        return httpx.Response(200, json=_reply(DATA))

    assert _call(_provider(handler, "allow")).data == DATA


@pytest.mark.parametrize(
    "payload",
    [
        _reply(calls=False, finish="stop"),
        _reply("not json"),
        _reply("[1, 2]"),
        _reply(DATA, finish="content_filter"),
        {"choices": [], "usage": {}},
    ],
)
def test_refusals_and_bad_output_return_no_data(payload: dict[str, Any]) -> None:
    assert _call(_provider(lambda req: httpx.Response(200, json=payload))).data is None


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (408, True), (500, True), (503, True), (400, False), (401, False)]
)
def test_http_errors(status: int, retryable: bool) -> None:
    p = _provider(
        lambda req: httpx.Response(
            status, json={"error": {"code": status, "message": "x"}}, headers={"retry-after": "7"}
        )
    )
    with pytest.raises(ProviderCallError) as e:
        _call(p)
    assert (e.value.retryable, e.value.retry_after) == (retryable, 7.0)
    assert str(e.value) == f"http_{status}" or f"http_{status}" in repr(e.value)


def test_http_200_carrying_an_error_object_is_an_error() -> None:
    p = _provider(lambda req: httpx.Response(200, json={"error": {"code": 429, "message": "rate limited"}}))
    with pytest.raises(ProviderCallError) as e:
        _call(p)
    assert e.value.retryable is True


def test_timeout_is_retryable_and_the_key_never_leaks() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderCallError) as e:
        _call(_provider(handler))
    assert e.value.retryable is True and KEY not in repr(e.value) and KEY not in str(e.value.__cause__)


def test_inline_refs_on_the_real_extraction_schema() -> None:
    out = _inline_refs(prompts.extraction_schema())
    text = json.dumps(out)
    assert "$ref" not in text and "$defs" not in text and "definitions" not in text
    assert out["type"] == "object" and out["properties"]


def test_inline_refs_refuses_cycles_and_unknown_references() -> None:
    with pytest.raises(ValueError, match="cannot inline"):
        _inline_refs({"$ref": "#/$defs/nope"})
    with pytest.raises(ValueError, match="cannot inline"):
        _inline_refs({"$defs": {"a": {"properties": {"x": {"$ref": "#/$defs/a"}}}}, "$ref": "#/$defs/a"})


def _settings(**kw: Any) -> Settings:
    return Settings(
        app_env="local",
        secret_key="0123456789012345678901234567890123456789012345678901234567890123",
        encryption_keys="k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        database_url="postgresql+psycopg://rebate_app:pwd@postgres:5432/rebate",
        s3_endpoint_url="http://localhost:8333",
        s3_public_endpoint_url="http://localhost:8333",
        s3_bucket="rebate-files",
        s3_access_key_id="minio",
        s3_secret_access_key="minio123",
        **kw,
    )


def test_make_provider_openrouter() -> None:
    s = _settings(ai_provider="openrouter", openrouter_api_key=SecretStr(KEY), ai_model="vendor/model:free")
    p = make_provider(s)
    assert isinstance(p, OpenRouterProvider) and p.name == "openrouter"
    assert s.openrouter_data_collection == "deny"


def test_make_provider_openrouter_needs_key_and_model() -> None:
    with pytest.raises(NotConfigured):
        make_provider(_settings(ai_provider="openrouter", ai_model="vendor/model:free"))
    with pytest.raises(NotConfigured, match="AI_MODEL"):
        make_provider(_settings(ai_provider="openrouter", openrouter_api_key=SecretStr(KEY)))


def test_empty_openrouter_environment_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AI_PROVIDER", "OPENROUTER_API_KEY", "OPENROUTER_DATA_COLLECTION"):
        monkeypatch.setenv(name, "")
    s = _settings()
    assert (s.ai_provider, s.openrouter_api_key, s.openrouter_data_collection) == ("anthropic", None, "deny")
