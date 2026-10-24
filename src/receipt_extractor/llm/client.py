"""The single audited path for model calls (TRD §S3, APPFLOW §S2).

``LLMClient.complete`` flow: alias + data-policy check → cache lookup → credentials →
daily budget → token-bucket limiter → bounded-concurrency provider call with a hard timeout →
retry with jittered backoff on 429/5xx/timeouts (honouring ``Retry-After``) → JSON-schema
validation with one repair attempt → accounting record per provider call → cache write.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from receipt_extractor.llm.cache import DiskCache, cache_key
from receipt_extractor.llm.errors import (
    DataPolicyError,
    LLMError,
    MissingCredentialsError,
    ProviderError,
    ProviderQuotaExhaustedError,
    ProviderRequestError,
    ProviderTimeoutError,
    RateLimitedError,
    RetriesExhaustedError,
    UnknownModelError,
    sanitize_error_text,
)
from receipt_extractor.llm.limits import BudgetTracker, RateLimiter, Sleep, utc_today
from receipt_extractor.llm.providers import (
    ChatCall,
    ChatResult,
    EmbedCall,
    EmbedResult,
    LiteLLMProvider,
    Provider,
)
from receipt_extractor.llm.registry import (
    ModelRegistry,
    ModelSpec,
    PriceTable,
    check_pricing_covers,
)
from receipt_extractor.llm.retry import RetryPolicy
from receipt_extractor.llm.sinks import CallSink
from receipt_extractor.llm.types import (
    CallTags,
    DataOrigin,
    EmbedRequest,
    EmbedResponse,
    LLMCallRecord,
    LLMRequest,
    LLMResponse,
    extract_json_text,
)

if TYPE_CHECKING:
    from receipt_extractor.config import Settings

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN: Final = 4
IMAGE_TOKEN_ESTIMATE: Final = 1500
MAX_VALIDATION_ERRORS: Final = 10
MAX_STAGE_CHARS: Final = 64
FREE_TIER_COST: Final = Decimal(0)  # only free tiers are allowed (CLAUDE.md §4)

# Provider 429 texts naming a per-day limit: Groq "tokens per day (TPD)", Gemini "...PerDay...".
_DAILY_QUOTA_RE: Final = re.compile(r"per[\s_-]?day|\b(?:TPD|RPD)\b|daily", re.IGNORECASE)
_PUBLIC_ONLY_ORIGINS: Final[frozenset[DataOrigin]] = frozenset({"public_dataset", "synthetic"})
_KEY_ENV_VARS: Final = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}

_DEFAULT_RETRY: Final = RetryPolicy()

REPAIR_PROMPT: Final = (
    "Your previous reply did not match the required JSON schema.\n"
    "Problems:\n{errors}\n"
    "Reply again with only the corrected JSON object. No prose, no code fences."
)


@dataclass(frozen=True, slots=True)
class Credentials:
    """Provider credentials, resolved per alias. Keys are excluded from ``repr``."""

    groq_api_key: str | None = field(default=None, repr=False)
    gemini_api_key: str | None = field(default=None, repr=False)
    ollama_base_url: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> Credentials:
        def unwrap(value: Any) -> str | None:
            secret = value.get_secret_value() if value is not None else None
            return secret or None

        return cls(
            groq_api_key=unwrap(settings.groq_api_key),
            gemini_api_key=unwrap(settings.gemini_api_key),
            ollama_base_url=str(settings.ollama_base_url).rstrip("/"),
        )

    def for_spec(self, spec: ModelSpec) -> tuple[str | None, str | None]:
        """``(api_key, api_base)`` for ``spec`` or :class:`MissingCredentialsError`."""
        match spec.provider:
            case "groq" | "gemini":
                key = self.groq_api_key if spec.provider == "groq" else self.gemini_api_key
                if not key:
                    raise MissingCredentialsError(
                        f"{_KEY_ENV_VARS[spec.provider]} is not set; alias '{spec.alias}' needs it"
                    )
                return key, None
            case "ollama":
                return None, self.ollama_base_url
            case _:
                raise UnknownModelError(f"alias '{spec.alias}' is not callable")

    def secrets(self) -> list[str]:
        return [s for s in (self.groq_api_key, self.gemini_api_key) if s]


class _CachedChat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["chat"]
    text: str
    tokens_in: int | None
    tokens_out: int | None
    finish_reason: str | None
    schema_valid: bool | None
    repaired: bool
    image_tokens_in: int | None = None  # absent from entries written before M2.1
    attempts: int | None = Field(default=None, ge=1)  # absent from entries before M2.2


class _CachedEmbed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["embed"]
    vectors: list[list[float]]
    tokens_in: int | None = Field(ge=0)


@dataclass(frozen=True, slots=True)
class _Outcome[T]:
    result: T
    retries: int
    latency_ms: int  # provider time incl. retry backoff, excluding wait_ms
    wait_ms: int = 0  # queued for the daily budget, rate limiter or a concurrency slot


def _elapsed_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


def _add(a: int | None, b: int | None) -> int | None:
    return None if a is None and b is None else (a or 0) + (b or 0)


def _add_cost(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None and b is None else (a or Decimal(0)) + (b or Decimal(0))


def _repair_stage(stage: str | None, attempt: int = 1) -> str:
    # The first repair keeps its pre-M2.2 name ("llm:repair"); later ones are numbered.
    name = "repair" if attempt == 1 else f"repair{attempt}"
    return (f"{stage}:{name}" if stage else name)[:MAX_STAGE_CHARS]


def validation_problems(model: type[BaseModel], text: str) -> str | None:
    """``None`` when ``text`` validates as ``model``; otherwise a short, input-free summary."""
    try:
        model.model_validate_json(extract_json_text(text))
    except ValidationError as exc:
        lines = [
            f"- {'.'.join(str(p) for p in item['loc']) or '<root>'}: {item['msg']}"
            for item in exc.errors(include_input=False, include_url=False)[:MAX_VALIDATION_ERRORS]
        ]
        return "\n".join(lines)
    return None


class LLMClient:
    """Async client over a :class:`Provider`; one instance per process (or per eval run)."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        pricing: PriceTable,
        provider: Provider,
        credentials: Credentials,
        cache: DiskCache | None = None,
        budget: BudgetTracker | None = None,
        sinks: Sequence[CallSink] = (),
        max_concurrency: int = 4,
        timeout_s: float = 60.0,
        retry: RetryPolicy = _DEFAULT_RETRY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        today: Callable[[], date] = utc_today,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be ≥ 1")
        check_pricing_covers(registry, pricing)
        self.registry = registry
        self._pricing = pricing
        self._provider = provider
        self._credentials = credentials
        self._cache = cache
        self._budget = budget
        self._sinks = tuple(sinks)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._timeout_s = timeout_s
        self._retry = retry
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not cryptography
        self._today = today
        self._limiters: dict[str, RateLimiter] = {}

    # --- public API -------------------------------------------------------------------

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run a chat completion through cache, limits, retries, validation and accounting."""
        spec = self._spec(request.model_alias, "chat", request.data_origin)
        if spec.max_output_tokens is not None and request.max_tokens > spec.max_output_tokens:
            raise ProviderRequestError(
                f"max_tokens {request.max_tokens} exceeds {spec.max_output_tokens} "
                f"for '{spec.alias}'"
            )
        schema = request.response_model.model_json_schema() if request.response_model else None
        key = None
        if self._cache is not None:
            payload: dict[str, Any] = {
                "op": "chat",
                "model": spec.model,
                "messages": [m.cache_repr() for m in request.messages],
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "seed": request.seed,
                "schema": schema,
            }
            if schema is not None and not request.repair:
                # Only when it changes the outcome, so existing cache keys stay valid.
                payload["repair"] = False
            if spec.options:
                payload["options"] = dict(sorted(spec.options.items()))
            key = cache_key(payload)
            allowed = request.max_repairs if schema is not None and request.repair else 0
            cached = await self._cached_chat(key, spec, request.tags, repairs_allowed=allowed)
            if cached is not None:
                return cached

        api_key, api_base = self._credentials.for_spec(spec)
        timeout_s = request.timeout_s or self._timeout_s
        messages = [m.to_provider() for m in request.messages]
        call = ChatCall(
            model=spec.model,
            messages=messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            timeout_s=timeout_s,
            seed=request.seed,
            json_schema=schema,
            schema_name=request.response_model.__name__ if request.response_model else "response",
            api_key=api_key,
            api_base=api_base,
            options=dict(spec.options),
        )
        estimate = self._estimate_chat(request)
        stage = request.tags.stage
        first = await self._run(
            spec,
            lambda: self._provider.complete(call),
            estimate=estimate,
            tokens_used=_chat_tokens,
            timeout_s=timeout_s,
            tags=request.tags,
            stage=stage,
        )
        if request.response_model is None:
            record = await self._emit_chat(spec, request.tags, stage, first, schema_valid=None)
            response = self._chat_response(
                spec, [(first, record)], schema_valid=None, repaired=False
            )
            await self._store_chat(key, response)
            return response

        problems = validation_problems(request.response_model, first.result.text)
        if problems is None or not request.repair:
            valid = problems is None
            record = await self._emit_chat(spec, request.tags, stage, first, schema_valid=valid)
            response = self._chat_response(
                spec, [(first, record)], schema_valid=valid, repaired=False
            )
            await self._store_chat(key, response)
            return response

        first_record = await self._emit_chat(spec, request.tags, stage, first, schema_valid=False)
        return await self._repair(
            spec,
            request,
            call,
            [(first, first_record)],
            problems,
            key=key,
            estimate=estimate,
            timeout_s=timeout_s,
        )

    async def _repair(
        self,
        spec: ModelSpec,
        request: LLMRequest,
        call: ChatCall,
        parts: list[tuple[_Outcome[ChatResult], LLMCallRecord]],
        problems: str,
        *,
        key: str | None,
        estimate: int,
        timeout_s: float,
    ) -> LLMResponse:
        """Up to ``request.max_repairs`` repair calls after an invalid first reply.

        Each repair appends the previous reply and its validation problems to the conversation
        so far (APPFLOW §S2). A repair *call* that fails returns the last invalid reply uncached,
        so a later rerun can try again.
        """
        assert request.response_model is not None  # noqa: S101 - guarded by caller
        conversation = list(call.messages)
        reply_text = parts[-1][0].result.text
        current: str | None = problems
        for attempt in range(1, request.max_repairs + 1):
            conversation += [
                {"role": "assistant", "content": reply_text or "(empty reply)"},
                {"role": "user", "content": REPAIR_PROMPT.format(errors=current)},
            ]
            repair_call = replace(call, messages=list(conversation))
            stage = _repair_stage(request.tags.stage, attempt)
            try:
                outcome = await self._run(
                    spec,
                    lambda c=repair_call: self._provider.complete(c),  # type: ignore[misc]
                    estimate=estimate,
                    tokens_used=_chat_tokens,
                    timeout_s=timeout_s,
                    tags=request.tags,
                    stage=stage,
                )
            except LLMError as exc:
                logger.warning("repair call for '%s' failed: %s", spec.alias, type(exc).__name__)
                return self._chat_response(spec, parts, schema_valid=False, repaired=len(parts) > 1)
            reply_text = outcome.result.text
            current = validation_problems(request.response_model, reply_text)
            record = await self._emit_chat(
                spec, request.tags, stage, outcome, schema_valid=current is None
            )
            parts.append((outcome, record))
            if current is None:
                break
        response = self._chat_response(spec, parts, schema_valid=current is None, repaired=True)
        await self._store_chat(key, response)
        return response

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        """Embed ``request.inputs`` through cache, limits, retries and accounting."""
        spec = self._spec(request.model_alias, "embedding", request.data_origin)
        key = None
        if self._cache is not None:
            key = cache_key({"op": "embed", "model": spec.model, "inputs": list(request.inputs)})
            cached = await self._cached_embed(key, spec, request.tags)
            if cached is not None:
                return cached

        api_key, api_base = self._credentials.for_spec(spec)
        timeout_s = request.timeout_s or self._timeout_s
        call = EmbedCall(
            model=spec.model,
            inputs=list(request.inputs),
            timeout_s=timeout_s,
            api_key=api_key,
            api_base=api_base,
        )
        estimate = max(1, sum(len(t) for t in request.inputs) // CHARS_PER_TOKEN)
        outcome = await self._run(
            spec,
            lambda: self._provider.embed(call),
            estimate=estimate,
            tokens_used=_embed_tokens,
            timeout_s=timeout_s,
            tags=request.tags,
            stage=request.tags.stage,
        )
        result = outcome.result
        cost = self._cost(spec, result.tokens_in, None)
        record = await self._emit(
            spec,
            request.tags,
            request.tags.stage,
            tokens_in=result.tokens_in,
            latency_ms=outcome.latency_ms,
            wait_ms=outcome.wait_ms,
            cache_hit=False,
            retries=outcome.retries,
            list_cost_usd=cost,
        )
        if key is not None and self._cache is not None:
            await self._cache.set(
                key, {"op": "embed", "vectors": result.vectors, "tokens_in": result.tokens_in}
            )
        return EmbedResponse(
            model_alias=spec.alias,
            provider=spec.provider,
            model=spec.model,
            vectors=tuple(tuple(v) for v in result.vectors),
            tokens_in=result.tokens_in,
            latency_ms=outcome.latency_ms,
            wait_ms=outcome.wait_ms,
            cache_hit=False,
            retries=outcome.retries,
            list_cost_usd=cost,
            actual_cost_usd=FREE_TIER_COST,
            call_ids=(record.id,),
        )

    # --- policy -----------------------------------------------------------------------

    def _spec(
        self, alias: str, kind: Literal["chat", "embedding"], origin: DataOrigin
    ) -> ModelSpec:
        spec = self.registry.get(alias)
        if spec.kind != kind:
            raise UnknownModelError(f"alias '{alias}' is a {spec.kind} model, not {kind}")
        if spec.data_policy == "public_only" and origin not in _PUBLIC_ONLY_ORIGINS:
            raise DataPolicyError(
                f"alias '{alias}' accepts public dataset or synthetic content only "
                f"(request data_origin is '{origin}')"
            )
        return spec

    def _limiter(self, spec: ModelSpec) -> RateLimiter:
        limiter = self._limiters.get(spec.alias)
        if limiter is None:
            limiter = RateLimiter(spec.limits, clock=self._clock, sleep=self._sleep)
            self._limiters[spec.alias] = limiter
        return limiter

    @staticmethod
    def _estimate_chat(request: LLMRequest) -> int:
        chars = sum(m.text_chars() for m in request.messages)
        images = sum(m.image_count() for m in request.messages)
        return chars // CHARS_PER_TOKEN + images * IMAGE_TOKEN_ESTIMATE + request.max_tokens

    def _cost(
        self, spec: ModelSpec, tokens_in: int | None, tokens_out: int | None
    ) -> Decimal | None:
        return self._pricing.cost(spec.model, tokens_in, tokens_out, self._today())

    # --- execution --------------------------------------------------------------------

    async def _run[T](
        self,
        spec: ModelSpec,
        op: Callable[[], Awaitable[T]],
        *,
        estimate: int,
        tokens_used: Callable[[T], int | None],
        timeout_s: float,
        tags: CallTags,
        stage: str | None,
    ) -> _Outcome[T]:
        """Call ``op`` with budget, limiter, semaphore, timeout and retries.

        On final failure an error record is emitted and the error is raised.
        """
        limiter = self._limiter(spec)
        start = time.perf_counter()
        waited_s = 0.0
        failures = 0
        while True:
            queued = time.perf_counter()
            if self._budget is not None:
                await self._budget.reserve(spec.alias, spec.limits, estimate)
            reserved = await limiter.acquire(estimate)
            error: ProviderError
            try:
                async with self._semaphore:
                    waited_s += time.perf_counter() - queued
                    async with asyncio.timeout(timeout_s):
                        result = await op()
            except TimeoutError:
                error = ProviderTimeoutError(f"no response within {timeout_s:g}s")
            except ProviderError as exc:
                error = exc
            else:
                used = tokens_used(result)
                actual = used if used is not None else reserved
                limiter.settle(reserved, actual)
                if self._budget is not None:
                    await self._budget.settle(spec.alias, estimate, actual)
                wait_ms = round(waited_s * 1000)
                return _Outcome(result, failures, max(0, _elapsed_ms(start) - wait_ms), wait_ms)

            if self._budget is not None:  # the request counts; failed calls consume no tokens
                await self._budget.settle(spec.alias, estimate, 0)
            failures += 1
            final: LLMError | None = None
            if not error.retryable:
                final = error
            elif self._is_daily_quota(error):
                # The provider's own daily window (it need not match the local UTC tracker):
                # waiting hours inside a call is never right; the runner pauses on this.
                final = ProviderQuotaExhaustedError(spec.alias, error.retry_after_s)
            elif failures >= self._retry.max_attempts:
                final = RetriesExhaustedError(spec.alias, failures, error)
            if final is not None:
                wait_ms = round(waited_s * 1000)
                await self._emit(
                    spec,
                    tags,
                    stage,
                    latency_ms=max(0, _elapsed_ms(start) - wait_ms),
                    wait_ms=wait_ms,
                    cache_hit=False,
                    retries=failures - 1,
                    error=sanitize_error_text(
                        f"{type(final).__name__}: {final}", self._credentials.secrets()
                    ),
                )
                raise final
            delay = self._retry.delay(failures, error.retry_after_s, self._rng)
            logger.warning(
                "llm call to '%s' failed (%s, status %s); retry %d/%d in %.2fs",
                spec.alias,
                type(error).__name__,
                error.status_code,
                failures,
                self._retry.max_attempts - 1,
                delay,
            )
            await self._sleep(delay)

    def _is_daily_quota(self, error: ProviderError) -> bool:
        """A 429 that will not clear within a call: a far ``Retry-After`` or a per-day limit."""
        if not isinstance(error, RateLimitedError):
            return False
        far = (
            error.retry_after_s is not None and error.retry_after_s > self._retry.max_retry_after_s
        )
        return far or _DAILY_QUOTA_RE.search(str(error)) is not None

    # --- cache ------------------------------------------------------------------------

    async def _cached_chat(
        self, key: str, spec: ModelSpec, tags: CallTags, *, repairs_allowed: int = 0
    ) -> LLMResponse | None:
        """The cached response for ``key``, if it is what this request would produce.

        ``max_repairs`` is not part of the key (so raising it re-sends nothing that was valid),
        so an entry is reused only when consistent with the request's repair budget: a valid
        reply that needed no more repairs than allowed, or an invalid one that used exactly the
        allowed repairs. Anything else is a miss, and the new result replaces the entry.
        """
        assert self._cache is not None  # noqa: S101 - guarded by caller
        start = time.perf_counter()
        raw = await self._cache.get(key)
        if raw is None:
            return None
        try:
            entry = _CachedChat.model_validate(raw)
        except ValidationError:
            logger.warning("llm cache entry has an unexpected shape; ignoring it")
            return None
        attempts = entry.attempts or (2 if entry.repaired else 1)
        repairs_used = attempts - 1
        if entry.schema_valid is False:
            usable = repairs_used == repairs_allowed
        else:
            usable = repairs_used <= repairs_allowed or entry.schema_valid is None
        if not usable:
            return None
        cost = self._cost(spec, entry.tokens_in, entry.tokens_out)
        latency = _elapsed_ms(start)
        record = await self._emit(
            spec,
            tags,
            tags.stage,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
            image_tokens_in=entry.image_tokens_in,
            latency_ms=latency,
            cache_hit=True,
            retries=0,
            schema_valid=entry.schema_valid,
            list_cost_usd=cost,
        )
        return LLMResponse(
            model_alias=spec.alias,
            provider=spec.provider,
            model=spec.model,
            text=entry.text,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
            image_tokens_in=entry.image_tokens_in,
            finish_reason=entry.finish_reason,
            latency_ms=latency,
            cache_hit=True,
            retries=0,
            schema_valid=entry.schema_valid,
            repaired=entry.repaired,
            attempts=attempts,
            list_cost_usd=cost,
            actual_cost_usd=FREE_TIER_COST,
            call_ids=(record.id,),
        )

    async def _cached_embed(
        self, key: str, spec: ModelSpec, tags: CallTags
    ) -> EmbedResponse | None:
        assert self._cache is not None  # noqa: S101 - guarded by caller
        start = time.perf_counter()
        raw = await self._cache.get(key)
        if raw is None:
            return None
        try:
            entry = _CachedEmbed.model_validate(raw)
        except ValidationError:
            logger.warning("llm cache entry has an unexpected shape; ignoring it")
            return None
        cost = self._cost(spec, entry.tokens_in, None)
        latency = _elapsed_ms(start)
        record = await self._emit(
            spec,
            tags,
            tags.stage,
            tokens_in=entry.tokens_in,
            latency_ms=latency,
            cache_hit=True,
            retries=0,
            list_cost_usd=cost,
        )
        return EmbedResponse(
            model_alias=spec.alias,
            provider=spec.provider,
            model=spec.model,
            vectors=tuple(tuple(v) for v in entry.vectors),
            tokens_in=entry.tokens_in,
            latency_ms=latency,
            cache_hit=True,
            retries=0,
            list_cost_usd=cost,
            actual_cost_usd=FREE_TIER_COST,
            call_ids=(record.id,),
        )

    async def _store_chat(self, key: str | None, response: LLMResponse) -> None:
        if key is None or self._cache is None:
            return
        await self._cache.set(
            key,
            _CachedChat(
                op="chat",
                text=response.text,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                finish_reason=response.finish_reason,
                schema_valid=response.schema_valid,
                repaired=response.repaired,
                image_tokens_in=response.image_tokens_in,
                attempts=response.attempts,
            ).model_dump(),
        )

    # --- accounting -------------------------------------------------------------------

    async def _emit_chat(
        self,
        spec: ModelSpec,
        tags: CallTags,
        stage: str | None,
        outcome: _Outcome[ChatResult],
        *,
        schema_valid: bool | None,
    ) -> LLMCallRecord:
        result = outcome.result
        return await self._emit(
            spec,
            tags,
            stage,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            image_tokens_in=result.image_tokens_in,
            latency_ms=outcome.latency_ms,
            wait_ms=outcome.wait_ms,
            cache_hit=False,
            retries=outcome.retries,
            schema_valid=schema_valid,
            list_cost_usd=self._cost(spec, result.tokens_in, result.tokens_out),
        )

    async def _emit(
        self,
        spec: ModelSpec,
        tags: CallTags,
        stage: str | None,
        *,
        latency_ms: int,
        cache_hit: bool,
        retries: int,
        wait_ms: int = 0,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        image_tokens_in: int | None = None,
        schema_valid: bool | None = None,
        list_cost_usd: Decimal | None = None,
        error: str | None = None,
    ) -> LLMCallRecord:
        record = LLMCallRecord(
            run_id=tags.run_id,
            request_id=tags.request_id,
            case_id=tags.case_id,
            stage=stage,
            model_alias=spec.alias,
            provider=spec.provider,
            model=spec.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            image_tokens_in=image_tokens_in,
            latency_ms=latency_ms,
            wait_ms=wait_ms,
            cache_hit=cache_hit,
            retries=retries,
            schema_valid=schema_valid,
            list_cost_usd=list_cost_usd,
            actual_cost_usd=FREE_TIER_COST,
            error=error,
        )
        for sink in self._sinks:
            await sink.write(record)
        return record

    def _chat_response(
        self,
        spec: ModelSpec,
        parts: list[tuple[_Outcome[ChatResult], LLMCallRecord]],
        *,
        schema_valid: bool | None,
        repaired: bool,
    ) -> LLMResponse:
        last = parts[-1][0].result
        tokens_in: int | None = None
        tokens_out: int | None = None
        image_tokens_in: int | None = None
        cost: Decimal | None = None
        for outcome, record in parts:
            tokens_in = _add(tokens_in, outcome.result.tokens_in)
            tokens_out = _add(tokens_out, outcome.result.tokens_out)
            image_tokens_in = _add(image_tokens_in, outcome.result.image_tokens_in)
            cost = _add_cost(cost, record.list_cost_usd)
        return LLMResponse(
            model_alias=spec.alias,
            provider=spec.provider,
            model=spec.model,
            text=last.text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            image_tokens_in=image_tokens_in,
            finish_reason=last.finish_reason,
            latency_ms=sum(outcome.latency_ms for outcome, _ in parts),
            wait_ms=sum(outcome.wait_ms for outcome, _ in parts),
            cache_hit=False,
            retries=sum(outcome.retries for outcome, _ in parts),
            schema_valid=schema_valid,
            repaired=repaired,
            attempts=len(parts),
            list_cost_usd=cost,
            actual_cost_usd=FREE_TIER_COST,
            call_ids=tuple(record.id for _, record in parts),
        )


def _chat_tokens(result: ChatResult) -> int | None:
    return _add(result.tokens_in, result.tokens_out)


def _embed_tokens(result: EmbedResult) -> int | None:
    return result.tokens_in


def build_client(
    settings: Settings,
    *,
    cache: bool,
    sinks: Sequence[CallSink] = (),
    provider: Provider | None = None,
    budget: bool = True,
) -> LLMClient:
    """Production wiring from :class:`Settings`.

    ``cache`` is ON for eval runs and OFF for live API traffic (TRD §S3). ``budget`` enables
    the persisted daily RPD/TPD tracker.
    """
    registry = ModelRegistry.from_yaml(settings.llm_models_path)
    pricing = PriceTable.from_yaml(settings.llm_pricing_path)
    return LLMClient(
        registry=registry,
        pricing=pricing,
        provider=provider or LiteLLMProvider(),
        credentials=Credentials.from_settings(settings),
        cache=DiskCache(settings.llm_cache_dir) if cache else None,
        budget=BudgetTracker(settings.llm_budget_path) if budget else None,
        sinks=sinks,
        max_concurrency=settings.llm_max_concurrency,
        timeout_s=settings.llm_timeout_s,
    )
