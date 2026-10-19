"""LLMClient behaviour against FakeProvider: cache, retries, structured output, cost, policy."""

from __future__ import annotations

import asyncio
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


async def test_retry_after_beyond_cap_fails_fast(make_client: Factory, clock: Any) -> None:
    provider = FakeProvider([FakeFailure("rate_limit", retry_after_s=3600.0), FakeReply()])
    with pytest.raises(RateLimitedError):
        await make_client(provider).complete(chat())
    assert len(provider.calls) == 1
    assert clock.sleeps == []


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
