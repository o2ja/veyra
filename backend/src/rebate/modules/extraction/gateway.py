"""S6.2 the AI gateway: the single place that talks to an LLM (AI_PROCESSING.md §2, §4, §9, §10).

* ``Provider`` is the interface; ``AnthropicProvider`` the first adapter (structured output through one
  forced tool call; no other tools are ever offered to the model).
* ``Gateway.call`` enforces, in order: the per-workspace opt-in (``ai_enabled`` from tenant settings), the
  per-tenant daily cost/token caps, retries with exponential backoff and jitter (``tenacity``, ``Retry-After``
  honoured), one repair attempt when the output does not validate, and a cost row per call in
  ``ai_invocations`` written under the restricted role ``rebate_ai_writer`` (own transaction: a failed
  extraction still records what it cost).
* Logs carry ids, hashes and counts only. Prompts, documents and responses are never logged or stored here.
* OD-2 is unresolved: nothing in this module runs against a real provider unless an API key is configured;
  tests drive the adapter through recorded responses at the HTTP transport (``HTTP_CLIENT_FACTORY``).
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import anthropic
import structlog
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from rebate.core import clock
from rebate.core.config import Settings
from rebate.core.db import transaction
from rebate.core.ids import new_id
from rebate.modules.extraction.models import AiInvocation

log = structlog.get_logger("rebate.ai")

# Tests install a factory returning the SDK HTTP client with a recorded-response transport. Production: None.
HTTP_CLIENT_FACTORY: Callable[[], Any] | None = None  # returns the SDK's HTTP client (tests only)


class GatewayError(Exception):
    code = "gateway_error"


class AiDisabled(GatewayError):
    code = "ai_disabled"


class NotConfigured(GatewayError):
    code = "provider_not_configured"


class CapReached(GatewayError):
    code = "daily_cap_reached"


class ProviderUnavailable(GatewayError):
    code = "provider_unavailable"


class ProviderBusy(GatewayError):
    """The workspace's AI slot is taken (or the pool is exhausted): retryable, never a 500."""

    code = "provider_busy"


class ProviderFailure(GatewayError):
    code = "provider_error"


class ProviderRefused(GatewayError):
    code = "provider_refused"


class InvalidOutput(GatewayError):
    code = "invalid_output"


class ProviderCallError(Exception):
    def __init__(self, code: str, *, retryable: bool, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


@dataclass(frozen=True)
class ProviderResponse:
    data: dict[str, Any] | None  # the tool input; None = refusal / no tool call
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    name: str

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout: float,
    ) -> ProviderResponse: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, http_client: Any = None) -> None:
        # retries are ours (tenacity), so the SDK must not retry on its own
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=http_client)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout: float,
    ) -> ProviderResponse:
        try:
            msg = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "name": tool_name,
                        "description": "Record the extraction result.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
                timeout=timeout,
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderCallError("timeout", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retry_after = None
            raw = exc.response.headers.get("retry-after")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None
            retryable = exc.status_code in (408, 409, 429) or exc.status_code >= 500
            raise ProviderCallError(
                f"http_{exc.status_code}", retryable=retryable, retry_after=retry_after
            ) from exc
        data = next((b.input for b in msg.content if b.type == "tool_use" and b.name == tool_name), None)
        refused = msg.stop_reason == "refusal" or not isinstance(data, dict)
        return ProviderResponse(
            None if refused else dict(data),  # type: ignore[arg-type]
            msg.usage.input_tokens,
            msg.usage.output_tokens,
        )


def make_provider(settings: Settings) -> Provider:
    if settings.anthropic_api_key is None:
        raise NotConfigured("no AI provider key is configured")
    client = HTTP_CLIENT_FACTORY() if HTTP_CLIENT_FACTORY is not None else None
    return AnthropicProvider(settings.anthropic_api_key.get_secret_value(), client)


def cost_usd(settings: Settings, input_tokens: int, output_tokens: int) -> Decimal:
    million = Decimal(1_000_000)
    return (
        Decimal(input_tokens) * settings.ai_price_input_per_mtok
        + Decimal(output_tokens) * settings.ai_price_output_per_mtok
    ) / million


@dataclass(frozen=True)
class CallResult:
    data: dict[str, Any]
    repaired: bool
    model: str


Validate = Callable[[dict[str, Any]], None]  # raises ValueError(message) when the output is unusable


class Gateway:
    def __init__(
        self,
        settings: Settings,
        ai_engine: Engine,
        provider: Provider,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings, self.engine, self.provider, self._sleep = settings, ai_engine, provider, sleep
        self.invocations: list[dict[str, Any]] = []  # content-free summaries, for the callers' audit events

    def take_invocations(self) -> list[dict[str, Any]]:
        out, self.invocations = self.invocations, []
        return out

    # ---- caps ----------------------------------------------------------------------------------------

    @staticmethod
    def _usage(db: Any, tenant_id: UUID) -> tuple[Decimal, int]:
        start = datetime.combine(clock.now().astimezone(UTC).date(), dtime.min, tzinfo=UTC)
        cost, tokens = db.execute(
            select(
                func.coalesce(func.sum(AiInvocation.cost_usd), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(AiInvocation.input_tokens, 0)
                        + func.coalesce(AiInvocation.output_tokens, 0)
                    ),
                    0,
                ),
            ).where(AiInvocation.tenant_id == tenant_id, AiInvocation.created_at >= start)
        ).one()
        return Decimal(cost), int(tokens)

    def usage_today(self, tenant_id: UUID) -> tuple[Decimal, int]:
        with transaction(self.engine, tenant_id) as db:
            return self._usage(db, tenant_id)

    def _check_caps(self, db: Any, tenant_id: UUID) -> None:
        cost, tokens = self._usage(db, tenant_id)
        if cost >= self.settings.ai_daily_cost_cap_usd or tokens >= self.settings.ai_daily_token_cap:
            raise CapReached("daily AI cap reached")

    # ---- one provider call with retries ---------------------------------------------------------------

    def _wait(self, state: RetryCallState) -> float:
        outcome = state.outcome
        exc = outcome.exception() if outcome is not None else None
        if isinstance(exc, ProviderCallError) and exc.retry_after is not None:
            return min(exc.retry_after, self.settings.ai_backoff_max_seconds)
        return float(
            wait_exponential_jitter(
                initial=self.settings.ai_backoff_base_seconds, max=self.settings.ai_backoff_max_seconds
            )(state)
        )

    def _attempt(
        self,
        model: str,
        system: str,
        user: str,
        tool: str,
        schema: dict[str, Any],
        attempts: int,
        timeout: float,
    ) -> ProviderResponse:
        retrying = Retrying(
            stop=stop_after_attempt(attempts),
            wait=self._wait,
            retry=retry_if_exception(lambda e: isinstance(e, ProviderCallError) and e.retryable),
            sleep=self._sleep,
            reraise=True,
        )
        return retrying(
            self.provider.complete,
            model=model,
            system=system,
            user=user,
            tool_name=tool,
            schema=schema,
            max_tokens=self.settings.ai_max_output_tokens,
            timeout=timeout,
        )

    def _record(
        self,
        db: Any,
        tenant_id: UUID,
        extraction_id: UUID | None,
        purpose: str,
        model: str,
        prompt_version: str,
        digest: str,
        status: str,
        started: float,
        resp: ProviderResponse | None,
        error_code: str | None,
    ) -> None:
        tokens_in = resp.input_tokens if resp else None
        tokens_out = resp.output_tokens if resp else None
        cost = cost_usd(self.settings, tokens_in or 0, tokens_out or 0)
        latency = int((time.monotonic() - started) * 1000)
        row_id = new_id()
        db.add(
            AiInvocation(
                tenant_id=tenant_id,
                id=row_id,
                extraction_id=extraction_id,
                purpose=purpose,
                provider=self.provider.name,
                model=model,
                prompt_version=prompt_version,
                input_sha256=digest,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=cost,
                latency_ms=latency,
                status=status,
                error_code=error_code,
                created_at=clock.now(),
            )
        )
        self.invocations.append(
            {
                "id": row_id,
                "extraction_id": extraction_id,
                "purpose": purpose,
                "model": model,
                "status": status,
                "cost_usd": format(cost, "f"),
                "usage_in": tokens_in,
                "usage_out": tokens_out,
                "failure": error_code,
            }
        )
        log.info(
            "ai.invocation",
            tenant_id=str(tenant_id),
            extraction_id=str(extraction_id) if extraction_id else None,
            purpose=purpose,
            model=model,
            input_sha256=digest,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=str(cost),
            status=status,
            error_code=error_code,
        )

    def _one(
        self,
        tenant_id: UUID,
        extraction_id: UUID | None,
        purpose: str,
        model: str,
        prompt_version: str,
        system: str,
        user: str,
        tool: str,
        schema: dict[str, Any],
        attempts: int,
        timeout: float,
        wait: bool,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(f"{system}\n\x1f{user}".encode()).hexdigest()
        # One transaction per call: the advisory lock serialises a tenant's calls, so the cap check below sees
        # every earlier call's ledger row (no check-then-call race) and caps hold across worker processes.
        try:
            return self._locked_call(
                tenant_id, extraction_id, purpose, model, prompt_version, digest, system, user, tool, schema,
                attempts, timeout, wait,
            )  # fmt: skip
        except SATimeoutError as exc:  # connection pool exhausted: busy, not a 500
            raise ProviderBusy("no database connection available") from exc

    def _take_slot(self, db: Any, tenant_id: UUID, wait: bool) -> None:
        key = {"k": f"ai:{tenant_id}"}
        if not wait:  # request path: never queue behind another call
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), key):
                raise ProviderBusy("another AI call of this workspace is in progress")
            return
        millis = int(self.settings.ai_lock_timeout_seconds * 1000)  # an int: safe to inline
        db.execute(text(f"SET LOCAL lock_timeout = {millis}"))
        try:
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), key)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":  # lock_not_available
                raise ProviderBusy("another AI call of this workspace is in progress") from exc
            raise
        db.execute(text("SET LOCAL lock_timeout = 0"))

    def _locked_call(
        self,
        tenant_id: UUID,
        extraction_id: UUID | None,
        purpose: str,
        model: str,
        prompt_version: str,
        digest: str,
        system: str,
        user: str,
        tool: str,
        schema: dict[str, Any],
        attempts: int,
        timeout: float,
        wait: bool,
    ) -> dict[str, Any]:
        failure: GatewayError | None = None
        status, code, resp = "ok", None, None
        with transaction(self.engine, tenant_id) as db:
            self._take_slot(db, tenant_id, wait)
            self._check_caps(db, tenant_id)  # CapReached: rolled back, nothing recorded, no call made
            started = time.monotonic()
            try:
                resp = self._attempt(model, system, user, tool, schema, attempts, timeout)
            except ProviderCallError as exc:
                status, code, failure = "error", exc.code, ProviderUnavailable(exc.code)
            except Exception as exc:  # SDK or response-parsing failure: still a ledger row and a GatewayError
                status, code, failure = (
                    "error",
                    f"sdk_{type(exc).__name__}"[:60],
                    ProviderFailure(type(exc).__name__),
                )
            else:
                if resp.data is None:
                    status, failure = "refused", ProviderRefused("the model returned no usable output")
            self._record(
                db,
                tenant_id,
                extraction_id,
                purpose,
                model,
                prompt_version,
                digest,
                status,
                started,
                resp,
                code,
            )
        if failure is not None:
            raise failure
        if resp is None or resp.data is None:  # unreachable: failure is set in both cases
            raise ProviderFailure("no response")
        return resp.data

    def call(
        self,
        *,
        ai_enabled: bool,
        tenant_id: UUID,
        extraction_id: UUID | None,
        purpose: str,
        model: str,
        prompt_version: str,
        system: str,
        user: str,
        tool: str,
        schema: dict[str, Any],
        validate: Validate | None = None,
        repair: bool = True,
        repair_purpose: str = "agreement_repair",
        max_attempts: int | None = None,
        timeout: float | None = None,
        wait_for_lock: bool = True,
    ) -> CallResult:
        """One logical model call (+ at most one repair). Raises a ``GatewayError`` subclass on failure.
        ``max_attempts`` / ``timeout`` tighten the limits for synchronous callers (request path)."""
        if not ai_enabled:
            raise AiDisabled("AI processing is not enabled for this workspace")
        attempts = max_attempts or self.settings.ai_max_attempts
        seconds = timeout or self.settings.ai_request_timeout_seconds
        data = self._one(
            tenant_id,
            extraction_id,
            purpose,
            model,
            prompt_version,
            system,
            user,
            tool,
            schema,
            attempts,
            seconds,
            wait_for_lock,
        )
        if validate is None:
            return CallResult(data, False, model)
        try:
            validate(data)
            return CallResult(data, False, model)
        except ValueError as first:
            problem = str(first)[:1000]
        if not repair:
            raise InvalidOutput(problem[:500])
        repair_user = (
            f"{user}\n\nYour previous answer was rejected by the validator: {problem}\n"
            "Return a corrected answer through the tool. Do not add anything the document does not state."
        )
        data = self._one(
            tenant_id,
            extraction_id,
            repair_purpose,
            model,
            prompt_version,
            system,
            repair_user,
            tool,
            schema,
            attempts,
            seconds,
            wait_for_lock,
        )
        try:
            validate(data)
        except ValueError as second:
            raise InvalidOutput(str(second)[:500]) from second
        return CallResult(data, True, model)
