"""The AI gateway against a real database and the real ``rebate_ai_writer`` role; the provider answers from
the transport-level replay. Expected costs are hand-derived from the configured prices
($3 / $15 per million tokens): 1000 in + 200 out = 0.003 + 0.003 = 0.006."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rebate.core.config import Settings
from rebate.core.db import make_engine, transaction
from rebate.modules.extraction import gateway, prompts
from rebate.modules.extraction.gateway import AnthropicProvider, Gateway
from tests.conftest import DbUrls
from tests.extraction.conftest import ai_overrides, ai_url
from tests.extraction.replay import Replay, error, ok, timeout
from tests.integration.conftest import Workspace
from tests.support import make_settings

VALID: dict[str, Any] = {
    "schema_version": 1,
    "header": {},
    "rules": {},
    "customers": [],
    "items": [],
    "tiers": [],
}
SYSTEM, USER = prompts.build_agreement_prompt("a", "SECRET DOCUMENT BODY 4711")


def _validate(data: dict[str, Any]) -> None:
    from rebate.modules.extraction.service import _check_format

    _check_format(data)


class Rig:
    def __init__(self, db_urls: DbUrls, replay: Replay, tenant: UUID, **overrides: Any) -> None:
        self.settings: Settings = make_settings(db_urls.app, **ai_overrides(db_urls, **overrides))
        self.engine: Engine = make_engine(ai_url(db_urls))
        self.sleeps: list[float] = []
        self.replay, self.tenant = replay, tenant
        provider = AnthropicProvider("sk-replay", replay.client())
        self.gw = Gateway(self.settings, self.engine, provider, sleep=self.sleeps.append)

    def call(self, *, enabled: bool = True, validate: Any = None, user: str = USER) -> gateway.CallResult:
        return self.gw.call(
            ai_enabled=enabled,
            tenant_id=self.tenant,
            extraction_id=None,
            purpose="agreement_pass_a",
            model="claude-sonnet-5-5",
            prompt_version=prompts.PROMPT_VERSION,
            system=SYSTEM,
            user=user,
            tool=prompts.TOOL_NAME,
            schema=prompts.extraction_schema(),
            validate=validate,
        )

    def ledger(self) -> list[dict[str, Any]]:
        with transaction(self.engine, self.tenant) as db:
            rows = db.execute(
                text(
                    "SELECT purpose, status, error_code, input_tokens, output_tokens, cost_usd, input_sha256 "
                    "FROM ai_invocations ORDER BY created_at, id"
                )
            ).mappings()
            return [dict(r) for r in rows]


@pytest.fixture
def rig(ws: Workspace, replay: Replay, migrated_db: DbUrls) -> Rig:
    return Rig(migrated_db, replay, UUID(ws.tenant_id))


def test_success_records_cost_and_hash_but_never_the_prompt(rig: Rig, replay: Replay) -> None:
    replay.script.append(ok(VALID))
    with structlog.testing.capture_logs() as logs:
        result = rig.call()
    assert result.data == VALID and not result.repaired
    (row,) = rig.ledger()
    assert (row["status"], row["input_tokens"], row["output_tokens"]) == ("ok", 1000, 200)
    assert row["cost_usd"] == Decimal("0.006000")
    assert len(row["input_sha256"]) == 64
    assert "SECRET DOCUMENT BODY 4711" not in str(logs) and "SECRET DOCUMENT BODY 4711" not in str(row)
    assert any(e["event"] == "ai.invocation" and e["input_sha256"] == row["input_sha256"] for e in logs)


def test_request_forces_one_tool_and_sends_no_credentials_beyond_the_header(rig: Rig, replay: Replay) -> None:
    replay.script.append(ok(VALID))
    rig.call()
    body = replay.calls[0]
    assert body["tool_choice"] == {"type": "tool", "name": "record_extraction"}
    assert [t["name"] for t in body["tools"]] == ["record_extraction"]
    assert (
        body["model"] == "claude-sonnet-5-5" and "SECRET DOCUMENT BODY 4711" in body["messages"][0]["content"]
    )


def test_prompt_and_tool_schema_require_iso_dates() -> None:
    import re

    assert "YYYY-MM-DD" in SYSTEM and prompts.PROMPT_VERSION == "agreement-v2"
    props = prompts.extraction_schema()["properties"]
    dates = [props["header"]["properties"][k]["properties"]["value"] for k in ("valid_from", "valid_to")]
    period = props["rules"]["properties"]["measurement_period"]["properties"]["value"]["properties"]
    dates += [period["start"], period["end"]]
    for node in dates:
        assert re.fullmatch(node["pattern"], "2026-01-01")
        assert not re.fullmatch(node["pattern"], "January 1, 2026")


def test_retries_with_exponential_backoff_then_succeeds(
    ws: Workspace, replay: Replay, migrated_db: DbUrls
) -> None:
    rig = Rig(
        migrated_db, replay, UUID(ws.tenant_id), ai_backoff_base_seconds=2.0, ai_backoff_max_seconds=30.0
    )
    replay.script.extend([error(529), timeout, ok(VALID)])
    assert rig.call().data == VALID
    assert len(replay.calls) == 3 and len(rig.sleeps) == 2
    # tenacity exponential jitter: 2 * 2**(n-1) plus up to 1 s of jitter
    assert 2.0 <= rig.sleeps[0] <= 3.0 and 4.0 <= rig.sleeps[1] <= 5.0
    assert [r["status"] for r in rig.ledger()] == ["ok"]  # one logical call, one ledger row


def test_retry_after_header_is_honoured(ws: Workspace, replay: Replay, migrated_db: DbUrls) -> None:
    rig = Rig(migrated_db, replay, UUID(ws.tenant_id), ai_backoff_max_seconds=30.0)
    replay.script.extend([error(429, {"retry-after": "7"}), ok(VALID)])
    rig.call()
    assert rig.sleeps == [7.0]


def test_gives_up_after_the_configured_attempts_and_records_the_failure(rig: Rig, replay: Replay) -> None:
    replay.script.extend([error(503)] * 4)
    with pytest.raises(gateway.ProviderUnavailable):
        rig.call()
    assert len(replay.calls) == 4  # AI_PROCESSING.md §10: up to 4 attempts
    (row,) = rig.ledger()
    assert (row["status"], row["error_code"], row["cost_usd"]) == ("error", "http_503", Decimal(0))


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "provider_request_rejected"),
        (401, "provider_auth_failed"),
        (403, "provider_auth_failed"),
        (404, "provider_request_rejected"),
        (422, "provider_request_rejected"),
    ],
)
def test_permanent_client_errors_fail_fast_with_their_own_category(
    rig: Rig, replay: Replay, status: int, code: str
) -> None:
    replay.script.extend([error(status)] * 4)
    with pytest.raises(gateway.ProviderRejected) as caught:
        rig.call()
    assert len(replay.calls) == 1 and rig.sleeps == []  # no retry, no backoff
    assert caught.value.code == code and f"HTTP {status}" in str(caught.value)
    assert not isinstance(caught.value, gateway.ProviderUnavailable)
    assert "could not be reached" not in str(caught.value)
    (row,) = rig.ledger()
    assert (row["status"], row["error_code"], row["cost_usd"]) == ("error", f"http_{status}", Decimal(0))


@pytest.mark.parametrize("status", [408, 409, 429, 500, 529])
def test_transient_statuses_are_still_retried_and_stay_unavailable(
    rig: Rig, replay: Replay, status: int
) -> None:
    replay.script.extend([error(status), ok(VALID)])
    assert rig.call().data == VALID
    assert len(replay.calls) == 2


NO_FORCED_TOOL = 'tool_choice: type "tool" and "any" are not supported for this model.'


@pytest.fixture(autouse=True)
def _forget_models_that_reject_forced_tools() -> Iterator[None]:
    gateway._AUTO_TOOL_CHOICE.clear()
    yield
    gateway._AUTO_TOOL_CHOICE.clear()  # process-wide state: never leak into other modules


def test_models_that_reject_a_forced_tool_are_called_with_auto_and_remembered(
    rig: Rig, replay: Replay
) -> None:
    replay.script.extend([error(400, message=NO_FORCED_TOOL), ok(VALID), ok(VALID)])
    assert rig.call().data == VALID
    first, second = replay.calls
    assert first["tool_choice"] == {"type": "tool", "name": "record_extraction"}
    assert second["tool_choice"] == {"type": "auto"}
    assert second["system"].endswith("Answer only by calling the record_extraction tool.")
    assert [r["status"] for r in rig.ledger()] == ["ok"]  # one logical call, one ledger row, real tokens
    rig.call()  # the next call does not repeat the rejected request
    assert [c["tool_choice"]["type"] for c in replay.calls] == ["tool", "auto", "auto"]


def test_auto_tool_choice_still_validates_the_output_strictly(rig: Rig, replay: Replay) -> None:
    replay.script.extend([error(400, message=NO_FORCED_TOOL), ok({"bogus": 1}), ok({"bogus": 2})])
    with pytest.raises(gateway.InvalidOutput):
        rig.call(validate=_validate)
    assert all(c["tool_choice"]["type"] == "auto" for c in replay.calls[1:])


def test_auto_without_a_tool_call_is_a_refusal_not_a_guess(rig: Rig, replay: Replay) -> None:
    from httpx2 import Response

    text_only = {
        "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": "Here is the answer in prose."}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 9},
    }  # fmt: skip
    replay.script.extend([error(400, message=NO_FORCED_TOOL), lambda _b: Response(200, json=text_only)])
    with pytest.raises(gateway.ProviderRefused):
        rig.call()


def test_an_unrelated_400_does_not_trigger_the_auto_fallback(rig: Rig, replay: Replay) -> None:
    replay.script.append(error(400, message="max_tokens: too large"))
    with pytest.raises(gateway.ProviderRejected):
        rig.call()
    assert len(replay.calls) == 1 and not gateway._AUTO_TOOL_CHOICE


def test_invalid_output_gets_exactly_one_repair_attempt(rig: Rig, replay: Replay) -> None:
    replay.script.extend([ok({"bogus": 1}), ok(VALID)])
    result = rig.call(validate=_validate)
    assert result.repaired and result.data == VALID
    assert [r["purpose"] for r in rig.ledger()] == ["agreement_pass_a", "agreement_repair"]
    repair_prompt = replay.calls[1]["messages"][0]["content"]
    assert "rejected by the validator" in repair_prompt and "schema_version must be 1" in repair_prompt


def test_the_reason_for_a_repair_is_logged_without_document_text(rig: Rig, replay: Replay) -> None:
    replay.script.extend([ok({"bogus": 1}), ok(VALID)])
    with structlog.testing.capture_logs() as logs:
        rig.call(validate=_validate)
    (repair,) = [e for e in logs if e["event"] == "ai.repair"]
    assert "schema_version must be 1" in repair["reason"] and repair["purpose"] == "agreement_pass_a"
    assert repair["model"] == "claude-sonnet-5-5" and len(repair["reason"]) <= 300
    assert "SECRET DOCUMENT BODY 4711" not in str(logs)
    replay.script.append(ok(VALID))
    with structlog.testing.capture_logs() as logs:
        rig.call(validate=_validate)
    assert not [e for e in logs if e["event"] == "ai.repair"]  # a valid first answer logs nothing


def test_prompt_maps_nearest_cent_to_half_up() -> None:
    assert '"rounded to the nearest cent"' in SYSTEM and '"half_up"' in SYSTEM and "scale 2" in SYSTEM


def test_repair_that_still_fails_is_an_error_not_a_guess(rig: Rig, replay: Replay) -> None:
    replay.script.extend([ok({"bogus": 1}), ok({"bogus": 2})])
    with pytest.raises(gateway.InvalidOutput):
        rig.call(validate=_validate)
    assert len(replay.calls) == 2  # no second repair


def test_refusal_or_missing_tool_call_is_reported(rig: Rig, replay: Replay) -> None:
    from httpx2 import Response

    text_only = {
        "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": "I cannot help with that."}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 9},
    }  # fmt: skip
    replay.script.append(lambda _b: Response(200, json=text_only))
    with pytest.raises(gateway.ProviderRefused):
        rig.call()
    assert [r["status"] for r in rig.ledger()] == ["refused"]


def test_daily_cost_cap_blocks_the_next_call_without_calling_the_provider(
    ws: Workspace, replay: Replay, migrated_db: DbUrls
) -> None:
    rig = Rig(migrated_db, replay, UUID(ws.tenant_id), ai_daily_cost_cap_usd=Decimal("0.006"))
    replay.script.append(ok(VALID))
    rig.call()  # spends exactly 0.006
    with pytest.raises(gateway.CapReached):
        rig.call()
    assert len(replay.calls) == 1 and len(rig.ledger()) == 1


def test_the_budget_resets_with_the_next_utc_day(
    ws: Workspace, replay: Replay, migrated_db: DbUrls, clk: Any
) -> None:
    rig = Rig(migrated_db, replay, UUID(ws.tenant_id), ai_daily_cost_cap_usd=Decimal("0.006"))
    replay.script.extend([ok(VALID), ok(VALID)])
    rig.call()
    with pytest.raises(gateway.CapReached):
        rig.call()
    clk.advance(86400 + 60)
    rig.call()
    assert len(replay.calls) == 2


def test_daily_token_cap_blocks_too(ws: Workspace, replay: Replay, migrated_db: DbUrls) -> None:
    rig = Rig(migrated_db, replay, UUID(ws.tenant_id), ai_daily_token_cap=1200)
    replay.script.append(ok(VALID))
    rig.call()  # 1000 + 200 tokens
    with pytest.raises(gateway.CapReached):
        rig.call()
    assert len(replay.calls) == 1


def test_caps_are_per_tenant(ws: Workspace, replay: Replay, migrated_db: DbUrls) -> None:
    from tests.integration.conftest import new_workspace
    from tests.support import api_client

    rig = Rig(migrated_db, replay, UUID(ws.tenant_id), ai_daily_cost_cap_usd=Decimal("0.006"))
    replay.script.extend([ok(VALID), ok(VALID)])
    rig.call()
    with api_client(ws.app) as other_api:
        other = new_workspace(ws.app, other_api, master=False)
        rig.tenant = UUID(other.tenant_id)
        rig.call()  # a different workspace is not affected by the first one's spend
    assert len(replay.calls) == 2


def test_disabled_workspace_never_reaches_the_provider(rig: Rig, replay: Replay) -> None:
    with pytest.raises(gateway.AiDisabled):
        rig.call(enabled=False)
    assert replay.calls == [] and rig.ledger() == []


def test_without_a_key_there_is_no_provider(migrated_db: DbUrls) -> None:
    settings = make_settings(
        migrated_db.app, anthropic_api_key=None
    )  # a key in the dev .env must not leak in
    with pytest.raises(gateway.NotConfigured):
        gateway.make_provider(settings)


def test_the_ledger_is_append_only_even_for_the_ai_role(rig: Rig, replay: Replay) -> None:
    replay.script.append(ok(VALID))
    rig.call()
    from sqlalchemy.exc import DBAPIError

    for sql in ("UPDATE ai_invocations SET cost_usd = 0", "DELETE FROM ai_invocations"):
        with pytest.raises(DBAPIError), transaction(rig.engine, rig.tenant) as db:
            db.execute(text(sql))
    assert rig.ledger()[0]["cost_usd"] == Decimal("0.006000")
