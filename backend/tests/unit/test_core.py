"""config fail-fast, clock injection, UUIDv7 ids."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rebate.core import clock
from rebate.core.config import Settings
from rebate.core.ids import new_id
from tests.support import TEST_KEYS

VALID = {
    "APP_ENV": "ci",
    "SECRET_KEY": "k" * 40,
    "DATABASE_URL": "postgresql+psycopg://a@b/c",
    "ENCRYPTION_KEYS": TEST_KEYS,
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in (*VALID, "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_valid_environment_loads(env: pytest.MonkeyPatch) -> None:
    for k, v in VALID.items():
        env.setenv(k, v)
    s = Settings()
    assert s.app_env == "ci"
    assert "k" * 40 not in repr(s)  # secrets are masked


@pytest.mark.parametrize("missing", list(VALID))
def test_missing_required_value_fails(env: pytest.MonkeyPatch, missing: str) -> None:
    for k, v in VALID.items():
        if k != missing:
            env.setenv(k, v)
    with pytest.raises(ValidationError, match=missing.lower()):
        Settings()


@pytest.mark.parametrize(("key", "value"), [("SECRET_KEY", "short"), ("APP_ENV", "dev")])
def test_invalid_value_fails(env: pytest.MonkeyPatch, key: str, value: str) -> None:
    for k, v in {**VALID, key: value}.items():
        env.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()


def test_empty_ai_environment_variables_fallback_to_defaults(env: pytest.MonkeyPatch) -> None:
    for k, v in VALID.items():
        env.setenv(k, v)
    for k in (
        "AI_PROVIDER",
        "AI_MODEL",
        "AI_PRICE_INPUT_PER_MTOK",
        "AI_PRICE_OUTPUT_PER_MTOK",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        env.setenv(k, "")
    s = Settings()
    assert s.ai_provider == "anthropic"
    assert s.ai_model == "claude-sonnet-5-5"
    assert s.ai_price_input_per_mtok == Decimal("3.00")
    assert s.ai_price_output_per_mtok == Decimal("15.00")
    assert s.gemini_api_key is None
    assert s.anthropic_api_key is None


def test_clock_can_be_frozen_and_restored() -> None:
    at = datetime(2026, 1, 31, 23, 59, tzinfo=UTC)
    with clock.frozen(at):
        assert clock.now() == at
    assert clock.now() != at
    assert clock.now().tzinfo is UTC
    with pytest.raises(ValueError), clock.frozen(datetime(2026, 1, 1)):
        pass


def test_ids_are_uuid7_and_time_ordered() -> None:
    ids = [new_id() for _ in range(2000)]
    assert all(i.version == 7 for i in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
