"""LLMClient behaviour against FakeProvider: cache, retries, structured output, cost, policy."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from receipt_extractor.llm.client import Credentials, LLMClient
from receipt_extractor.llm.errors import (
    BudgetExhaustedError,
    DataPolicyError,
    MissingCredentialsError,
    ProviderQuotaExhaustedError,
    ProviderRequestError,
    RateLimitedError,
    RetriesExhaustedError,
    UnknownModelError,
)
from receipt_extractor.llm.fake import FakeFailure, FakeProvider, FakeReply
from receipt_extractor.llm.providers import ChatCall, ChatResult, EmbedCall, EmbedResult
from receipt_extractor.llm.sinks import MemorySink
from receipt_extractor.llm.types import (
    CallTags,
    EmbedRequest,
    ImagePart,
    LLMRequest,
    Message,
    TextPart,
)

Factory = Callable[..., LLMClient]


class Total(BaseModel):
    total: str
    currency: str


def chat(alias: str = "test-chat", **kwargs: Any) -> LLMRequest:
    fields: dict[str, Any] = {
        "model_alias": alias,
        "messages": (Message(role="user", content="extract the total"),),
        "data_origin": "public_dataset",
    }
    fields.update(kwargs)
    return LLMRequest(**fields)


# --- cache -------------------------------------------------------------------------------


async def test_cache_hit_returns_identical_response_without_calling_provider(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider([FakeReply(text="hello", tokens_in=12, tokens_out=3)])
    client = make_client(provider, cache=True)

    first = await client.complete(chat())
    second = await client.complete(chat())

    assert len(provider.calls) == 1
    assert second.cache_hit is True
    assert first.cache_hit is False
    for field in ("text", "tokens_in", "tokens_out", "schema_valid", "list_cost_usd", "model"):
        assert getattr(second, field) == getattr(first, field)
    assert [r.cache_hit for r in sink.records] == [False, True]
    assert second.call_ids != first.call_ids


async def test_cache_key_covers_params_and_schema(make_client: Factory) -> None:
    provider = FakeProvider(default=FakeReply(text='{"total": "1", "currency": "IDR"}'))
    client = make_client(provider, cache=True)

    await client.complete(chat())
    await client.complete(chat(temperature=0.5))
    await client.complete(chat(max_tokens=99))
    await client.complete(chat(response_model=Total))
    await client.complete(chat(tags=CallTags(case_id="other")))  # tags are not in the key

    assert len(provider.calls) == 4


async def test_cache_disabled_always_calls_provider(make_client: Factory) -> None:
    provider = FakeProvider()
    client = make_client(provider, cache=False)
    await client.complete(chat())
    await client.complete(chat())
    assert len(provider.calls) == 2


async def test_cache_hit_needs_no_credentials(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text="cached")])
    await make_client(provider, cache=True).complete(chat())

    offline = make_client(provider, cache=True, credentials=Credentials())
    response = await offline.complete(chat())

    assert response.cache_hit and response.text == "cached"
    assert len(provider.calls) == 1


# --- retries -----------------------------------------------------------------------------


async def test_429_then_success_records_one_retry(
    make_client: Factory, sink: MemorySink, clock: Any
) -> None:
    provider = FakeProvider([FakeFailure("rate_limit", retry_after_s=7.0), FakeReply()])
    client = make_client(provider)

    response = await client.complete(chat())

    assert response.retries == 1
    assert len(provider.calls) == 2
    assert len(sink.records) == 1
    assert sink.records[0].retries == 1
    assert sink.records[0].error is None
    assert clock.sleeps == [pytest.approx(7.0)]  # Retry-After is honoured as a floor


async def test_five_consecutive_failures_surface_an_error(
    make_client: Factory, sink: MemorySink, clock: Any
) -> None:
    provider = FakeProvider(default=FakeFailure("server_error"))
    client = make_client(provider)

    with pytest.raises(RetriesExhaustedError) as excinfo:
        await client.complete(chat(tags=CallTags(case_id="c1", stage="extract")))

    assert excinfo.value.attempts == 5
    assert len(provider.calls) == 5
    assert len(clock.sleeps) == 4
    # Exponential backoff with equal jitter: each delay lies in [step/2, step].
    for failures, delay in enumerate(clock.sleeps, start=1):
        step = min(30.0, 2.0 ** (failures - 1))
        assert step / 2 <= delay <= step
    [record] = sink.records
    assert record.retries == 4
    assert record.error is not None and "RetriesExhaustedError" in record.error
    assert record.case_id == "c1" and record.stage == "extract"
    assert record.tokens_in is None and record.cache_hit is False


@pytest.mark.parametrize("kind", ["rate_limit", "server_error", "timeout"])
async def test_retryable_failures_are_retried(make_client: Factory, kind: str) -> None:
    provider = FakeProvider([FakeFailure(kind), FakeFailure(kind), FakeReply()])  # type: ignore[arg-type]
    response = await make_client(provider).complete(chat())
    assert response.retries == 2


async def test_bad_request_is_not_retried(make_client: Factory, sink: MemorySink) -> None:
    provider = FakeProvider([FakeFailure("bad_request"), FakeReply()])
    with pytest.raises(ProviderRequestError):
        await make_client(provider).complete(chat())
    assert len(provider.calls) == 1
    assert sink.records[0].retries == 0
    assert sink.records[0].error is not None


async def test_retry_after_beyond_cap_fails_fast_as_quota(
    make_client: Factory, clock: Any, sink: MemorySink
) -> None:
    provider = FakeProvider([FakeFailure("rate_limit", retry_after_s=3600.0), FakeReply()])
    with pytest.raises(ProviderQuotaExhaustedError, match="retry after 3600 s"):
        await make_client(provider).complete(chat())
    assert len(provider.calls) == 1
    assert clock.sleeps == []
    assert sink.records[-1].error is not None  # the refused call is still accounted


async def test_per_day_429_text_is_a_quota_even_without_retry_after(
    make_client: Factory, clock: Any
) -> None:
    class DailyLimit(FakeProvider):
        async def complete(self, call: ChatCall) -> ChatResult:
            self.calls.append(call)
            raise RateLimitedError(
                "Rate limit reached on tokens per day (TPD): Limit 200000, Used 199749",
                status_code=429,
            )

    provider = DailyLimit()
    with pytest.raises(ProviderQuotaExhaustedError):
        await make_client(provider).complete(chat())
    assert len(provider.calls) == 1 and clock.sleeps == []  # no futile retries
    assert isinstance(ProviderQuotaExhaustedError("x", None), BudgetExhaustedError)  # pauses runs


async def test_per_minute_429_is_still_retried(make_client: Factory, clock: Any) -> None:
    provider = FakeProvider([FakeFailure("rate_limit", retry_after_s=2.0), FakeReply(text="ok")])
    response = await make_client(provider).complete(chat())
    assert response.text == "ok" and response.retries == 1 and clock.sleeps


async def test_hard_timeout_cancels_hung_call_and_retries(make_client: Factory) -> None:
    provider = FakeProvider([FakeFailure("hang"), FakeReply(text="late but fine")])
    response = await make_client(provider).complete(chat(timeout_s=0.05))
    assert response.text == "late but fine"
    assert response.retries == 1


# --- structured output -------------------------------------------------------------------


async def test_valid_json_first_time(make_client: Factory, sink: MemorySink) -> None:
    provider = FakeProvider([FakeReply(text='{"total": "12000", "currency": "IDR"}')])
    response = await make_client(provider).complete(chat(response_model=Total))

    assert response.schema_valid is True
    assert response.repaired is False
    assert response.parse(Total) == Total(total="12000", currency="IDR")
    assert provider.calls[0].json_schema == Total.model_json_schema()
    assert [r.schema_valid for r in sink.records] == [True]


async def test_code_fenced_json_is_accepted(make_client: Factory) -> None:
    fenced = '```json\n{"total": "5", "currency": "IDR"}\n```'
    response = await make_client(FakeProvider([FakeReply(text=fenced)])).complete(
        chat(response_model=Total)
    )
    assert response.schema_valid is True
    assert response.json_value() == {"total": "5", "currency": "IDR"}


async def test_invalid_json_repaired_successfully(make_client: Factory, sink: MemorySink) -> None:
    provider = FakeProvider(
        [
            FakeReply(text='{"total": "12000"', tokens_in=100, tokens_out=10),
            FakeReply(text='{"total": "12000", "currency": "IDR"}', tokens_in=150, tokens_out=12),
        ]
    )
    response = await make_client(provider).complete(
        chat(response_model=Total, tags=CallTags(stage="extract"))
    )

    assert response.schema_valid is True
    assert response.repaired is True
    assert response.tokens_in == 250 and response.tokens_out == 22
    assert len(provider.calls) == 2
    repair_messages = provider.calls[1].messages
    assert repair_messages[-2] == {"role": "assistant", "content": '{"total": "12000"'}
    assert "did not match the required JSON schema" in repair_messages[-1]["content"]
    assert [(r.stage, r.schema_valid) for r in sink.records] == [
        ("extract", False),
        ("extract:repair", True),
    ]
    assert response.call_ids == tuple(r.id for r in sink.records)


async def test_invalid_json_after_repair_sets_schema_valid_false(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider(
        [FakeReply(text="not json at all"), FakeReply(text='{"total": 1, "currency": null}')]
    )
    response = await make_client(provider).complete(chat(response_model=Total))

    assert response.schema_valid is False
    assert response.repaired is True
    assert len(provider.calls) == 2  # exactly one repair attempt
    assert [r.schema_valid for r in sink.records] == [False, False]
    with pytest.raises(ValueError, match="does not match Total"):
        response.parse(Total)


async def test_queue_wait_is_reported_apart_from_latency(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider(default=FakeReply(text="ok", latency_s=0.1))
    client = make_client(provider, max_concurrency=1)
    first, second = await asyncio.gather(client.complete(chat()), client.complete(chat()))

    queued, served = sorted((first, second), key=lambda r: r.wait_ms, reverse=True)
    assert queued.wait_ms >= 80  # waited for the only concurrency slot
    assert queued.latency_ms < 180  # its own provider time, not slot wait + call
    assert served.wait_ms < 50
    assert sorted(r.wait_ms for r in sink.records) == sorted((first.wait_ms, second.wait_ms))


async def test_repair_disabled_makes_exactly_one_call(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider([FakeReply(text="not json at all")])
    response = await make_client(provider).complete(chat(response_model=Total, repair=False))

    assert len(provider.calls) == 1
    assert provider.calls[0].json_schema == Total.model_json_schema()  # schema still sent
    assert response.schema_valid is False
    assert response.repaired is False
    assert [r.schema_valid for r in sink.records] == [False]


async def test_repair_disabled_valid_reply_and_separate_cache_entry(
    make_client: Factory,
) -> None:
    provider = FakeProvider(
        [FakeReply(text="{broken"), FakeReply(text='{"total": "1", "currency": "IDR"}')],
        default=FakeReply(text='{"total": "2", "currency": "IDR"}'),
    )
    client = make_client(provider, cache=True)

    naive = await client.complete(chat(response_model=Total, repair=False))
    assert naive.schema_valid is False and len(provider.calls) == 1
    again = await client.complete(chat(response_model=Total, repair=False))
    assert again.cache_hit is True and again.text == "{broken"  # invalid reply cached as is
    # The same request with repair ON must not reuse the naive (unrepaired) entry.
    repaired = await client.complete(chat(response_model=Total))
    assert repaired.cache_hit is False
    assert repaired.schema_valid is True
    assert len(provider.calls) == 2


async def test_repair_flag_does_not_change_cache_key_without_schema(
    make_client: Factory,
) -> None:
    provider = FakeProvider([FakeReply(text="hello")])
    client = make_client(provider, cache=True)
    await client.complete(chat())
    hit = await client.complete(chat(repair=False))
    assert hit.cache_hit is True
    assert len(provider.calls) == 1


async def test_repair_call_failure_returns_invalid_first_reply(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider([FakeReply(text="{broken"), FakeFailure("bad_request")])
    client = make_client(provider, cache=True)

    response = await client.complete(chat(response_model=Total))

    assert response.schema_valid is False and response.text == "{broken"
    assert sink.records[-1].error is not None
    # Not cached: the next attempt calls the provider again.
    provider.push(FakeReply(text='{"total": "1", "currency": "IDR"}'))
    again = await client.complete(chat(response_model=Total))
    assert again.cache_hit is False and again.schema_valid is True


async def test_no_schema_means_schema_valid_none(make_client: Factory) -> None:
    response = await make_client(FakeProvider()).complete(chat())
    assert response.schema_valid is None
    assert response.repaired is False


# --- accounting --------------------------------------------------------------------------


async def test_cost_is_tokens_times_dated_price(make_client: Factory, sink: MemorySink) -> None:
    # Price table: groq/test-model = $1.00 / $2.00 per 1M tokens.
    provider = FakeProvider([FakeReply(tokens_in=1234, tokens_out=567)])
    response = await make_client(provider).complete(chat())

    expected = Decimal("0.00236800")  # (1234 * 1.00 + 567 * 2.00) / 1e6
    assert response.list_cost_usd == expected
    assert sink.records[0].list_cost_usd == expected
    assert response.actual_cost_usd == Decimal(0)
    assert isinstance(response.list_cost_usd, Decimal)


async def test_unknown_usage_gives_no_cost(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(tokens_in=None, tokens_out=None)])
    response = await make_client(provider).complete(chat())
    assert response.list_cost_usd is None


async def test_record_carries_tags_and_never_prompt_text(
    make_client: Factory, sink: MemorySink
) -> None:
    run_id = uuid.uuid4()
    tags = CallTags(run_id=run_id, request_id="req-1", case_id="case-7", stage="extract")
    await make_client(FakeProvider()).complete(chat(tags=tags))

    [record] = sink.records
    assert (record.run_id, record.request_id, record.case_id, record.stage) == (
        run_id,
        "req-1",
        "case-7",
        "extract",
    )
    assert (record.model_alias, record.provider, record.model) == (
        "test-chat",
        "groq",
        "groq/test-model",
    )
    assert record.latency_ms >= 0
    assert "extract the total" not in record.model_dump_json()


# --- policy ------------------------------------------------------------------------------


async def test_user_upload_never_reaches_public_only_alias(make_client: Factory) -> None:
    provider = FakeProvider()
    client = make_client(provider)
    with pytest.raises(DataPolicyError):
        await client.complete(chat("test-public", data_origin="user_upload"))
    with pytest.raises(DataPolicyError):  # the default origin is the restrictive one
        await client.complete(
            LLMRequest(model_alias="test-public", messages=(Message(role="user", content="x"),))
        )
    assert provider.calls == []

    await client.complete(chat("test-public", data_origin="public_dataset"))
    await client.complete(chat("test-public", data_origin="synthetic"))
    assert len(provider.calls) == 2


async def test_missing_key_fails_fast_naming_the_variable(make_client: Factory) -> None:
    provider = FakeProvider()
    client = make_client(provider, credentials=Credentials())
    with pytest.raises(MissingCredentialsError, match="GROQ_API_KEY"):
        await client.complete(chat())
    assert provider.calls == []


async def test_api_key_and_base_are_passed_per_provider(make_client: Factory) -> None:
    provider = FakeProvider()
    client = make_client(provider)
    await client.complete(chat())
    await client.embed(EmbedRequest(model_alias="test-embed", inputs=("a",)))
    assert provider.calls[0].api_key == "gsk_TESTKEYtestkey0123456789"
    assert provider.calls[0].api_base is None
    assert provider.embed_calls[0].api_key is None
    assert provider.embed_calls[0].api_base == "http://localhost:11434"
    assert "gsk_" not in repr(provider.calls[0])


@pytest.mark.parametrize(
    ("alias", "error"),
    [
        ("nope", UnknownModelError),
        ("test-local", UnknownModelError),
        ("test-embed", UnknownModelError),
    ],
)
async def test_non_chat_aliases_are_refused(
    make_client: Factory, alias: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await make_client(FakeProvider()).complete(chat(alias))


async def test_max_tokens_above_model_limit_is_refused(make_client: Factory) -> None:
    from receipt_extractor.llm.registry import ModelRegistry, ModelSpec  # noqa: PLC0415

    spec = ModelSpec(
        alias="test-chat",
        provider="groq",
        kind="chat",
        model="groq/test-model",
        max_output_tokens=100,
    )
    client = make_client(FakeProvider(), registry=ModelRegistry({"test-chat": spec}))
    with pytest.raises(ProviderRequestError, match="max_tokens"):
        await client.complete(chat(max_tokens=101))


async def test_image_parts_are_sent_as_data_urls(make_client: Factory) -> None:
    provider = FakeProvider()
    image = ImagePart(mime_type="image/png", data=b"\x89PNG fake")
    message = Message(role="user", content=(TextPart(text="read this"), image))
    await make_client(provider).complete(chat("test-public", messages=(message,)))
    parts = provider.calls[0].messages[0]["content"]
    assert parts[0] == {"type": "text", "text": "read this"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


# --- budget / limiter / concurrency --------------------------------------------------------


async def test_daily_request_budget_pauses_before_the_provider(make_client: Factory) -> None:
    provider = FakeProvider()
    client = make_client(provider, budget=True)
    for _ in range(3):  # rpd = 3
        await client.complete(chat("test-limited", max_tokens=10))
    with pytest.raises(BudgetExhaustedError, match="request budget"):
        await client.complete(chat("test-limited", max_tokens=10))
    assert len(provider.calls) == 3


async def test_rpm_limiter_waits_instead_of_calling(make_client: Factory, clock: Any) -> None:
    provider = FakeProvider()
    client = make_client(provider)
    for _ in range(3):  # rpm = 2 → the third call waits ~30 s for one request to refill
        await client.complete(chat("test-limited", max_tokens=10))
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == pytest.approx(30.0)


async def test_concurrency_is_bounded_by_semaphore(make_client: Factory) -> None:
    in_flight = 0
    peak = 0

    class Slow:
        async def complete(self, call: ChatCall) -> ChatResult:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return ChatResult(text="ok", tokens_in=1, tokens_out=1)

        async def embed(self, call: EmbedCall) -> EmbedResult:
            raise NotImplementedError

    client = make_client(Slow(), max_concurrency=2)
    await asyncio.gather(*(client.complete(chat()) for _ in range(8)))
    assert peak == 2


# --- embeddings ----------------------------------------------------------------------------


async def test_embed_returns_vectors_and_caches(make_client: Factory, sink: MemorySink) -> None:
    provider = FakeProvider(embed_dim=4)
    client = make_client(provider, cache=True)
    request = EmbedRequest(model_alias="test-embed", inputs=("alpha", "beta"))

    first = await client.embed(request)
    second = await client.embed(request)

    assert len(first.vectors) == 2 and len(first.vectors[0]) == 4
    assert second.vectors == first.vectors
    assert second.cache_hit is True
    assert len(provider.embed_calls) == 1
    assert first.list_cost_usd == Decimal("0E-8")
    assert [r.cache_hit for r in sink.records] == [False, True]


async def test_embed_refuses_chat_alias(make_client: Factory) -> None:
    with pytest.raises(UnknownModelError):
        await make_client(FakeProvider()).embed(
            EmbedRequest(model_alias="test-chat", inputs=("a",))
        )


def test_client_requires_price_for_every_callable_alias() -> None:
    from receipt_extractor.llm.errors import LLMConfigError  # noqa: PLC0415
    from receipt_extractor.llm.registry import ModelRegistry, ModelSpec, PriceTable  # noqa: PLC0415

    spec = ModelSpec(alias="unpriced", provider="groq", kind="chat", model="groq/unpriced")
    with pytest.raises(LLMConfigError, match="no entry"):
        LLMClient(
            registry=ModelRegistry({"unpriced": spec}),
            pricing=PriceTable({}),
            provider=FakeProvider(),
            credentials=Credentials(),
        )


async def test_image_bytes_are_part_of_the_cache_key(make_client: Factory) -> None:
    provider = FakeProvider()
    client = make_client(provider, cache=True)

    def with_image(data: bytes) -> LLMRequest:
        image = ImagePart(mime_type="image/jpeg", data=data)
        message = Message(role="user", content=(TextPart(text="read"), image))
        return chat("test-public", messages=(message,))

    await client.complete(with_image(b"receipt-1"))
    await client.complete(with_image(b"receipt-1"))
    await client.complete(with_image(b"receipt-2"))
    assert len(provider.calls) == 2


async def test_cache_entry_of_unexpected_shape_is_a_miss(
    make_client: Factory, tmp_path: Any
) -> None:
    provider = FakeProvider([FakeReply(text="first"), FakeReply(text="second")])
    client = make_client(provider, cache=True)
    await client.complete(chat())
    [entry] = list((tmp_path / "cache").rglob("*.json"))
    entry.write_text('{"op": "chat", "unexpected": true}', encoding="utf-8")

    response = await client.complete(chat())

    assert response.cache_hit is False and response.text == "second"


# --- image tokens (M2.1) -----------------------------------------------------------------


async def test_image_tokens_summed_over_repair_and_recorded_per_call(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider(
        [
            FakeReply(text='{"total": "1"', tokens_in=1300, image_tokens_in=1120),
            FakeReply(
                text='{"total": "1", "currency": "IDR"}', tokens_in=1400, image_tokens_in=1120
            ),
        ]
    )
    response = await make_client(provider).complete(chat("test-public", response_model=Total))

    assert response.repaired is True
    assert response.image_tokens_in == 2240
    assert [r.image_tokens_in for r in sink.records] == [1120, 1120]


async def test_image_tokens_unknown_stay_none(make_client: Factory, sink: MemorySink) -> None:
    response = await make_client(FakeProvider([FakeReply(text="x")])).complete(chat())
    assert response.image_tokens_in is None
    assert sink.records[-1].image_tokens_in is None


async def test_cache_hit_keeps_image_tokens(make_client: Factory, sink: MemorySink) -> None:
    provider = FakeProvider([FakeReply(text="x", image_tokens_in=258)])
    client = make_client(provider, cache=True)
    await client.complete(chat("test-public"))
    cached = await client.complete(chat("test-public"))

    assert cached.cache_hit is True
    assert cached.image_tokens_in == 258
    assert sink.records[-1].cache_hit is True and sink.records[-1].image_tokens_in == 258


async def test_cache_entry_written_before_image_tokens_still_hits(
    make_client: Factory, tmp_path: Any
) -> None:
    provider = FakeProvider([FakeReply(text="first"), FakeReply(text="second")])
    client = make_client(provider, cache=True)
    await client.complete(chat())
    [entry] = list((tmp_path / "cache").rglob("*.json"))
    old = {
        "op": "chat",
        "text": "first",
        "tokens_in": 10,
        "tokens_out": 5,
        "finish_reason": "stop",
        "schema_valid": None,
        "repaired": False,
    }
    entry.write_text(json.dumps(old), encoding="utf-8")

    response = await client.complete(chat())

    assert response.cache_hit is True and response.text == "first"
    assert response.image_tokens_in is None


# --- alias options (M2.1: local Ollama vision model) --------------------------------------


def _registry_with_options(options: dict[str, Any]) -> Any:
    from receipt_extractor.llm.registry import ModelRegistry, ModelSpec  # noqa: PLC0415
    from tests.unit.conftest import make_registry  # noqa: PLC0415

    specs = {s.alias: s for s in make_registry()}
    specs["test-local-vl"] = ModelSpec(
        alias="test-local-vl",
        provider="ollama",
        kind="chat",
        model="ollama_chat/test-vl",
        options=options,
    )
    return ModelRegistry(specs)


def _pricing_with_local() -> Any:
    from datetime import date  # noqa: PLC0415

    from receipt_extractor.llm.registry import PriceEntry, PriceTable  # noqa: PLC0415

    def entry(inp: str, out: str) -> list[PriceEntry]:
        return [
            PriceEntry(
                effective_from=date(2026, 1, 1),
                input_per_mtok=Decimal(inp),
                output_per_mtok=Decimal(out),
                source="test",
                checked=date(2026, 1, 1),
            )
        ]

    return PriceTable(
        {
            "groq/test-model": entry("1.00", "2.00"),
            "gemini/test-flash": entry("0.50", "1.50"),
            "ollama/test-embed": entry("0", "0"),
            "ollama_chat/test-vl": entry("0", "0"),
        }
    )


async def test_alias_options_reach_the_provider_call(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text="x")])
    client = make_client(
        provider,
        registry=_registry_with_options({"num_ctx": 8192}),
        pricing=_pricing_with_local(),
    )
    await client.complete(chat("test-local-vl", timeout_s=600))

    (call,) = provider.calls
    assert call.options == {"num_ctx": 8192}
    assert call.timeout_s == 600
    assert call.api_base == "http://localhost:11434" and call.api_key is None


async def test_alias_options_are_part_of_the_cache_key_only_when_set(
    make_client: Factory,
) -> None:
    provider = FakeProvider()
    pricing = _pricing_with_local()
    plain = make_client(provider, cache=True, registry=_registry_with_options({}), pricing=pricing)
    await plain.complete(chat("test-local-vl"))
    await plain.complete(chat("test-local-vl"))
    assert len(provider.calls) == 1  # no options → same key as before options existed

    tuned = make_client(
        provider, cache=True, registry=_registry_with_options({"num_ctx": 8192}), pricing=pricing
    )
    await tuned.complete(chat("test-local-vl"))
    assert len(provider.calls) == 2  # different options → different answer, new key


# --- up to two repairs (M2.2) ---------------------------------------------------------------


async def test_second_repair_runs_when_the_first_is_still_invalid(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider(
        [
            FakeReply(text='{"total": "1"', tokens_in=100),
            FakeReply(text='{"total": 1}', tokens_in=110),
            FakeReply(text='{"total": "1", "currency": "IDR"}', tokens_in=120),
        ]
    )
    response = await make_client(provider).complete(
        chat(response_model=Total, max_repairs=2, tags=CallTags(stage="llm"))
    )

    assert response.schema_valid is True and response.repaired is True
    assert response.attempts == 3 and response.tokens_in == 330
    assert [(r.stage, r.schema_valid) for r in sink.records] == [
        ("llm", False),
        ("llm:repair", False),
        ("llm:repair2", True),
    ]
    # The second repair sees the whole conversation: both earlier replies and their errors.
    last = provider.calls[2].messages
    assert [m["role"] for m in last[-4:]] == ["assistant", "user", "assistant", "user"]
    assert last[-2]["content"] == '{"total": 1}'


async def test_repairs_stop_at_the_first_valid_reply(make_client: Factory) -> None:
    provider = FakeProvider(
        [FakeReply(text="nope"), FakeReply(text='{"total": "1", "currency": "IDR"}')]
    )
    response = await make_client(provider).complete(chat(response_model=Total, max_repairs=2))
    assert response.attempts == 2 and len(provider.calls) == 2


async def test_gives_up_after_max_repairs(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text="nope")] * 4)
    response = await make_client(provider).complete(chat(response_model=Total, max_repairs=2))
    assert response.schema_valid is False and response.attempts == 3
    assert len(provider.calls) == 3


async def test_failed_second_repair_returns_parts_so_far_uncached(make_client: Factory) -> None:
    provider = FakeProvider(
        [FakeReply(text="nope"), FakeReply(text="still nope"), FakeFailure("bad_request")]
    )
    client = make_client(provider, cache=True)
    response = await client.complete(chat(response_model=Total, max_repairs=2))

    assert response.schema_valid is False and response.repaired is True
    assert response.attempts == 2 and response.text == "still nope"
    provider.push(FakeReply(text='{"total": "1", "currency": "IDR"}'))
    again = await client.complete(chat(response_model=Total, max_repairs=2))
    assert again.cache_hit is False  # the failed attempt was not cached


VALID_TOTAL = '{"total": "1", "currency": "IDR"}'


async def test_valid_cached_reply_is_reused_under_a_larger_repair_budget(
    make_client: Factory,
) -> None:
    provider = FakeProvider([FakeReply(text=VALID_TOTAL)])
    client = make_client(provider, cache=True)
    await client.complete(chat(response_model=Total))  # max_repairs 1 (pre-M2.2 default)
    again = await client.complete(chat(response_model=Total, max_repairs=2))
    assert again.cache_hit and len(provider.calls) == 1  # nothing re-sent


async def test_invalid_cached_reply_is_retried_when_more_repairs_are_allowed(
    make_client: Factory,
) -> None:
    provider = FakeProvider([FakeReply(text="no"), FakeReply(text="no")])
    client = make_client(provider, cache=True)
    first = await client.complete(chat(response_model=Total, max_repairs=1))
    assert first.schema_valid is False and first.attempts == 2

    assert (await client.complete(chat(response_model=Total, max_repairs=1))).cache_hit
    provider.push(FakeReply(text="no"), FakeReply(text="no"), FakeReply(text=VALID_TOTAL))
    second = await client.complete(chat(response_model=Total, max_repairs=2))
    assert second.cache_hit is False and second.schema_valid is True and second.attempts == 3
    # The better answer replaced the entry.
    assert (await client.complete(chat(response_model=Total, max_repairs=2))).cache_hit


async def test_reply_that_needed_two_repairs_is_not_served_to_a_one_repair_request(
    make_client: Factory,
) -> None:
    provider = FakeProvider(
        [FakeReply(text="no"), FakeReply(text="no"), FakeReply(text=VALID_TOTAL)]
    )
    client = make_client(provider, cache=True)
    await client.complete(chat(response_model=Total, max_repairs=2))
    provider.push(FakeReply(text="no"), FakeReply(text="no"))
    strict = await client.complete(chat(response_model=Total, max_repairs=1))
    assert strict.cache_hit is False and strict.schema_valid is False and strict.attempts == 2


async def test_cache_hit_keeps_attempts_and_old_entries_infer_them(
    make_client: Factory, tmp_path: Any
) -> None:
    provider = FakeProvider([FakeReply(text="no"), FakeReply(text="no"), FakeReply(text="no")])
    client = make_client(provider, cache=True)
    await client.complete(chat(response_model=Total, max_repairs=2))
    cached = await client.complete(chat(response_model=Total, max_repairs=2))
    assert cached.cache_hit and cached.attempts == 3

    [entry] = list((tmp_path / "cache").rglob("*.json"))
    data = json.loads(entry.read_text(encoding="utf-8"))
    del data["attempts"]  # an entry written before M2.2
    data["repaired"] = True
    entry.write_text(json.dumps(data), encoding="utf-8")
    # Inferred: one repair (attempts 2), so it serves a one-repair request.
    old_entry = await client.complete(chat(response_model=Total, max_repairs=1))
    assert old_entry.cache_hit and old_entry.attempts == 2


@pytest.mark.parametrize("bad", [0, 3])
def test_max_repairs_is_bounded(bad: int) -> None:
    with pytest.raises(ValueError, match="max_repairs"):
        chat(max_repairs=bad)
