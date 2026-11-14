"""Tests for GeminiProvider, convert_schema, and make_provider (Task S6 / ai/gemini-provider).

Tests drive the provider adapter through recorded/fake HTTP transports (httpx.MockTransport).
No live API keys, no network calls. Key strings must never appear in logs or exceptions.
"""

import json
from typing import Any

import httpx
import pytest
import structlog
from pydantic import SecretStr

from rebate.core.config import Settings
from rebate.modules.extraction.gateway import (
    GeminiProvider,
    NotConfigured,
    ProviderCallError,
    convert_schema,
    make_provider,
)

SAMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agreement_type": {"enum": ["spa", "rebate", None]},
        "title": {"type": ["string", "null"]},
        "version": {"const": 1},
    },
    "required": ["agreement_type", "title"],
    "additionalProperties": False,
}

VALID_DATA: dict[str, Any] = {
    "agreement_type": "spa",
    "title": "Test Agreement",
    "version": 1,
}

DUMMY_KEY = "test-gemini-secret-key-12345"


def _make_gemini_response(
    content_obj: dict[str, Any] | None = None,
    *,
    raw_text: str | None = None,
    finish_reason: str = "STOP",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    empty_candidates: bool = False,
    safety_block: bool = False,
) -> dict[str, Any]:
    if empty_candidates:
        return {
            "candidates": [],
            "usageMetadata": {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
            },
        }

    if safety_block:
        return {
            "candidates": [
                {
                    "finishReason": "SAFETY",
                    "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "HIGH"}],
                }
            ],
            "usageMetadata": {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
            },
        }

    text_val = (
        raw_text if raw_text is not None else (json.dumps(content_obj) if content_obj is not None else "")
    )
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text_val}],
                    "role": "model",
                },
                "finishReason": finish_reason,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
        },
    }


def _mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------------------------------
# GeminiProvider unit tests
# --------------------------------------------------------------------------------------------------


def test_gemini_success() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=_make_gemini_response(VALID_DATA, input_tokens=120, output_tokens=45))

    client = _mock_client(handler)
    provider = GeminiProvider(DUMMY_KEY, http_client=client)

    resp = provider.complete(
        model="gemini-2.5-flash",
        system="You are an extraction assistant.",
        user="Extract document data.",
        tool_name="record_extraction",
        schema=SAMPLE_SCHEMA,
        max_tokens=2048,
        timeout=30.0,
    )

    assert resp.data == VALID_DATA
    assert resp.input_tokens == 120
    assert resp.output_tokens == 45

    assert len(captured_requests) == 1
    req = captured_requests[0]
    # Key must be in header, never in URL
    assert req.headers.get("x-goog-api-key") == DUMMY_KEY
    assert "key=" not in str(req.url)
    assert req.url.path == "/v1beta/models/gemini-2.5-flash:generateContent"

    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "You are an extraction assistant."
    assert body["contents"][0]["parts"][0]["text"] == "Extract document data."
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["maxOutputTokens"] == 2048
    assert "responseSchema" in body["generationConfig"]


def test_gemini_refusal_and_safety_block() -> None:
    # 1. Safety block
    client_safety = _mock_client(
        lambda _req: httpx.Response(200, json=_make_gemini_response(safety_block=True))
    )
    p_safety = GeminiProvider(DUMMY_KEY, http_client=client_safety)
    res_safety = p_safety.complete(
        model="gemini-2.5-flash",
        system="sys",
        user="usr",
        tool_name="tool",
        schema=SAMPLE_SCHEMA,
        max_tokens=1000,
        timeout=10.0,
    )
    assert res_safety.data is None
    assert res_safety.input_tokens == 1000

    # 2. Empty candidates
    client_empty = _mock_client(
        lambda _req: httpx.Response(200, json=_make_gemini_response(empty_candidates=True))
    )
    p_empty = GeminiProvider(DUMMY_KEY, http_client=client_empty)
    res_empty = p_empty.complete(
        model="gemini-2.5-flash",
        system="sys",
        user="usr",
        tool_name="tool",
        schema=SAMPLE_SCHEMA,
        max_tokens=1000,
        timeout=10.0,
    )
    assert res_empty.data is None

    # 3. Recitation block
    client_recitation = _mock_client(
        lambda _req: httpx.Response(
            200, json=_make_gemini_response(raw_text="...", finish_reason="RECITATION")
        )
    )
    p_recitation = GeminiProvider(DUMMY_KEY, http_client=client_recitation)
    res_rec = p_recitation.complete(
        model="gemini-2.5-flash",
        system="sys",
        user="usr",
        tool_name="tool",
        schema=SAMPLE_SCHEMA,
        max_tokens=1000,
        timeout=10.0,
    )
    assert res_rec.data is None


def test_gemini_invalid_json_returns_none() -> None:
    client = _mock_client(
        lambda _req: httpx.Response(
            200, json=_make_gemini_response(raw_text="I cannot extract this document. Not JSON.")
        )
    )
    provider = GeminiProvider(DUMMY_KEY, http_client=client)
    res = provider.complete(
        model="gemini-2.5-flash",
        system="sys",
        user="usr",
        tool_name="tool",
        schema=SAMPLE_SCHEMA,
        max_tokens=1000,
        timeout=10.0,
    )
    assert res.data is None


def test_gemini_429_with_retry_after() -> None:
    client = _mock_client(
        lambda _req: httpx.Response(429, headers={"retry-after": "12.5"}, json={"error": "rate limit"})
    )
    provider = GeminiProvider(DUMMY_KEY, http_client=client)

    with pytest.raises(ProviderCallError) as exc_info:
        provider.complete(
            model="gemini-2.5-flash",
            system="sys",
            user="usr",
            tool_name="tool",
            schema=SAMPLE_SCHEMA,
            max_tokens=1000,
            timeout=10.0,
        )

    err = exc_info.value
    assert err.code == "http_429"
    assert err.retryable is True
    assert err.retry_after == 12.5


def test_gemini_500_retryable() -> None:
    client = _mock_client(lambda _req: httpx.Response(503, json={"error": "service unavailable"}))
    provider = GeminiProvider(DUMMY_KEY, http_client=client)

    with pytest.raises(ProviderCallError) as exc_info:
        provider.complete(
            model="gemini-2.5-flash",
            system="sys",
            user="usr",
            tool_name="tool",
            schema=SAMPLE_SCHEMA,
            max_tokens=1000,
            timeout=10.0,
        )

    err = exc_info.value
    assert err.code == "http_503"
    assert err.retryable is True


def test_gemini_400_not_retryable() -> None:
    client = _mock_client(lambda _req: httpx.Response(400, json={"error": "bad request"}))
    provider = GeminiProvider(DUMMY_KEY, http_client=client)

    with pytest.raises(ProviderCallError) as exc_info:
        provider.complete(
            model="gemini-2.5-flash",
            system="sys",
            user="usr",
            tool_name="tool",
            schema=SAMPLE_SCHEMA,
            max_tokens=1000,
            timeout=10.0,
        )

    err = exc_info.value
    assert err.code == "http_400"
    assert err.retryable is False


def test_gemini_timeout_mapped_to_retryable_provider_call_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    client = _mock_client(handler)
    provider = GeminiProvider(DUMMY_KEY, http_client=client)

    with pytest.raises(ProviderCallError) as exc_info:
        provider.complete(
            model="gemini-2.5-flash",
            system="sys",
            user="usr",
            tool_name="tool",
            schema=SAMPLE_SCHEMA,
            max_tokens=1000,
            timeout=10.0,
        )

    err = exc_info.value
    assert err.code == "timeout"
    assert err.retryable is True


def test_gemini_key_never_appears_in_logs_or_exceptions() -> None:
    secret_key = "test-gemini-mock-key-must-never-leak-99"

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal failure"})

    client = _mock_client(handler)
    provider = GeminiProvider(secret_key, http_client=client)

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(Exception) as exc_info:
            provider.complete(
                model="gemini-2.5-flash",
                system="sys",
                user="usr",
                tool_name="tool",
                schema=SAMPLE_SCHEMA,
                max_tokens=1000,
                timeout=10.0,
            )

    exc_text = str(exc_info.value)
    logs_text = str(captured)
    assert secret_key not in exc_text
    assert secret_key not in logs_text


# --------------------------------------------------------------------------------------------------
# make_provider tests
# --------------------------------------------------------------------------------------------------


def test_make_provider_gemini_missing_key() -> None:
    settings = Settings(
        app_env="local",
        secret_key="0123456789012345678901234567890123456789012345678901234567890123",
        encryption_keys="k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        database_url="postgresql+psycopg://rebate_app:pwd@postgres:5432/rebate",
        s3_endpoint_url="http://localhost:8333",
        s3_public_endpoint_url="http://localhost:8333",
        s3_bucket="rebate-files",
        s3_access_key_id="minio",
        s3_secret_access_key="minio123",
        ai_provider="gemini",
        gemini_api_key=None,
        ai_model="gemini-2.5-flash",
    )
    with pytest.raises(NotConfigured, match="no AI provider key is configured"):
        make_provider(settings)


def test_make_provider_gemini_missing_ai_model_when_still_claude_default() -> None:
    settings = Settings(
        app_env="local",
        secret_key="0123456789012345678901234567890123456789012345678901234567890123",
        encryption_keys="k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        database_url="postgresql+psycopg://rebate_app:pwd@postgres:5432/rebate",
        s3_endpoint_url="http://localhost:8333",
        s3_public_endpoint_url="http://localhost:8333",
        s3_bucket="rebate-files",
        s3_access_key_id="minio",
        s3_secret_access_key="minio123",
        ai_provider="gemini",
        gemini_api_key=SecretStr("gemini-secret-key"),
        ai_model="claude-sonnet-5-5",  # Claude default!
    )
    with pytest.raises(NotConfigured, match="set AI_MODEL for the gemini provider"):
        make_provider(settings)


def test_make_provider_gemini_success() -> None:
    settings = Settings(
        app_env="local",
        secret_key="0123456789012345678901234567890123456789012345678901234567890123",
        encryption_keys="k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        database_url="postgresql+psycopg://rebate_app:pwd@postgres:5432/rebate",
        s3_endpoint_url="http://localhost:8333",
        s3_public_endpoint_url="http://localhost:8333",
        s3_bucket="rebate-files",
        s3_access_key_id="minio",
        s3_secret_access_key="minio123",
        ai_provider="gemini",
        gemini_api_key=SecretStr("gemini-secret-key"),
        ai_model="gemini-2.5-flash",
    )
    provider = make_provider(settings)
    assert provider.name == "gemini"
    assert isinstance(provider, GeminiProvider)


def test_make_provider_anthropic_default_preserved() -> None:
    settings = Settings(
        app_env="local",
        secret_key="0123456789012345678901234567890123456789012345678901234567890123",
        encryption_keys="k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        database_url="postgresql+psycopg://rebate_app:pwd@postgres:5432/rebate",
        s3_endpoint_url="http://localhost:8333",
        s3_public_endpoint_url="http://localhost:8333",
        s3_bucket="rebate-files",
        s3_access_key_id="minio",
        s3_secret_access_key="minio123",
        ai_provider="anthropic",
        anthropic_api_key=SecretStr("sk-ant-test-key"),
        ai_model="claude-sonnet-5-5",
    )
    provider = make_provider(settings)
    assert provider.name == "anthropic"


# --------------------------------------------------------------------------------------------------
# Schema converter unit tests
# --------------------------------------------------------------------------------------------------


def test_convert_schema_inlines_defs() -> None:
    schema = {
        "$defs": {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
                "required": ["street"],
            }
        },
        "type": "object",
        "properties": {
            "shipping": {"$ref": "#/$defs/Address"},
        },
        "required": ["shipping"],
    }
    converted = convert_schema(schema)

    assert "$defs" not in converted
    shipping = converted["properties"]["shipping"]
    assert shipping["type"] == "object"
    assert shipping["properties"]["street"]["type"] == "string"
    assert shipping["required"] == ["street"]


def test_convert_schema_inlines_definitions() -> None:
    schema = {
        "definitions": {
            "Item": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
            }
        },
        "type": "object",
        "properties": {
            "item": {"$ref": "#/definitions/Item"},
        },
    }
    converted = convert_schema(schema)
    assert "definitions" not in converted
    assert converted["properties"]["item"]["properties"]["sku"]["type"] == "string"


def test_convert_schema_simplifies_anyof_with_null() -> None:
    schema = {
        "type": "object",
        "properties": {
            "notes": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "null"},
                ]
            }
        },
    }
    converted = convert_schema(schema)
    notes = converted["properties"]["notes"]
    assert notes["type"] == "string"
    assert notes["nullable"] is True


def test_convert_schema_handles_type_list_with_null() -> None:
    schema = {
        "type": "object",
        "properties": {
            "quantity": {"type": ["integer", "null"]},
            "price": {"type": ["number", "null"]},
            "active": {"type": ["boolean", "null"]},
        },
    }
    converted = convert_schema(schema)
    props = converted["properties"]
    assert props["quantity"]["type"] == "integer"
    assert props["quantity"]["nullable"] is True
    assert props["price"]["type"] == "number"
    assert props["price"]["nullable"] is True
    assert props["active"]["type"] == "boolean"
    assert props["active"]["nullable"] is True


def test_convert_schema_translates_const_to_enum() -> None:
    schema = {
        "type": "object",
        "properties": {
            "schema_version": {"const": 1},
            "kind": {"const": "standard"},
        },
    }
    converted = convert_schema(schema)
    props = converted["properties"]
    assert props["schema_version"]["enum"] == [1]
    assert props["schema_version"]["type"] == "integer"
    assert props["kind"]["enum"] == ["standard"]
    assert props["kind"]["type"] == "string"


def test_convert_schema_handles_enum_with_none() -> None:
    schema = {
        "type": "object",
        "properties": {
            "status": {"enum": ["draft", "active", None]},
        },
    }
    converted = convert_schema(schema)
    status = converted["properties"]["status"]
    assert status["enum"] == ["draft", "active"]
    assert status["nullable"] is True
    assert status["type"] == "string"
