"""LiteLLM adapter: request shaping, response parsing and error mapping (no network)."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from receipt_extractor.llm.errors import (
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)
from receipt_extractor.llm.providers import (
    SCHEMA_REJECTED,
    ChatCall,
    EmbedCall,
    LiteLLMProvider,
    _litellm,
    map_provider_exception,
    provider_schema,
    schema_rejection_text,
)

API_KEY = "gsk_TESTKEYtestkey0123456789"  # fake value


def test_importing_the_layer_does_not_import_litellm() -> None:
    code = (
        "import sys, receipt_extractor.cli, receipt_extractor.llm;print('litellm' in sys.modules)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == "False"


@pytest.fixture(scope="module")
def litellm() -> Any:
    return _litellm()


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "https://api.example/v1")
    )


def test_rate_limit_maps_with_retry_after(litellm: Any) -> None:
    exc = litellm.exceptions.RateLimitError(
        message=f"limit reached for key {API_KEY}",
        llm_provider="groq",
        model="m",
        response=_response(429, {"retry-after": "12"}),
    )
    mapped = map_provider_exception(exc, secrets=[API_KEY])
    assert isinstance(mapped, RateLimitedError)
    assert mapped.retryable and mapped.retry_after_s == 12.0
    assert API_KEY not in str(mapped)


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (
            lambda m: m.exceptions.Timeout(message="slow", model="m", llm_provider="groq"),
            ProviderTimeoutError,
        ),
        (
            lambda m: m.exceptions.ServiceUnavailableError(
                message="down", llm_provider="groq", model="m", response=_response(503)
            ),
            ProviderUnavailableError,
        ),
        (
            lambda m: m.exceptions.APIConnectionError(
                message="refused", llm_provider="ollama", model="m"
            ),
            ProviderUnavailableError,
        ),
        (
            lambda m: m.exceptions.BadRequestError(
                message="bad schema", model="m", llm_provider="groq", response=_response(400)
            ),
            ProviderRequestError,
        ),
        (
            lambda m: m.exceptions.AuthenticationError(
                message="invalid key", llm_provider="groq", model="m", response=_response(401)
            ),
            ProviderRequestError,
        ),
        (lambda m: ValueError("unexpected"), ProviderRequestError),
    ],
)
def test_error_mapping(litellm: Any, factory: Any, expected: type[Exception]) -> None:
    mapped = map_provider_exception(factory(litellm), secrets=[])
    assert type(mapped) is expected
    assert mapped.retryable == (expected is not ProviderRequestError)


def test_gemini_key_in_url_is_redacted(litellm: Any) -> None:
    key = "AIzaSyTESTtestTESTtest0123456789"
    exc = litellm.exceptions.BadRequestError(
        message=f"400 for url https://generativelanguage.googleapis.com/v1beta/x?key={key}",
        model="m",
        llm_provider="gemini",
        response=_response(400),
    )
    mapped = map_provider_exception(exc, secrets=[])  # caught by pattern even without the key
    assert key not in str(mapped)


async def test_complete_shapes_request_and_parses_response(
    litellm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        message = SimpleNamespace(content='{"a": 1}')
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4),
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    call = ChatCall(
        model="groq/x",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=50,
        timeout_s=9.0,
        seed=7,
        json_schema={"type": "object"},
        schema_name="Receipt",
        api_key=API_KEY,
    )
    result = await LiteLLMProvider().complete(call)

    assert (result.text, result.tokens_in, result.tokens_out, result.finish_reason) == (
        '{"a": 1}',
        11,
        4,
        "stop",
    )
    assert seen["num_retries"] == 0
    assert seen["timeout"] == 9.0
    assert seen["seed"] == 7
    assert seen["api_key"] == API_KEY
    assert "api_base" not in seen
    assert seen["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Receipt", "schema": {"type": "object"}},
    }
    assert result.image_tokens_in is None  # not reported → unknown, not 0


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        (SimpleNamespace(image_tokens=1120, text_tokens=900), 1120),
        (SimpleNamespace(image_tokens=0), 0),
        (SimpleNamespace(image_tokens=None), None),
        (SimpleNamespace(image_tokens=-3), None),
        (SimpleNamespace(image_tokens="1120"), None),
        (None, None),
    ],
)
async def test_complete_reads_image_tokens_when_reported(
    litellm: Any, monkeypatch: pytest.MonkeyPatch, details: Any, expected: int | None
) -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        message = SimpleNamespace(content="{}")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=2020, completion_tokens=40, prompt_tokens_details=details
            ),
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    call = ChatCall(
        model="gemini/x",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=50,
        timeout_s=9.0,
    )
    result = await LiteLLMProvider().complete(call)
    assert result.tokens_in == 2020
    assert result.image_tokens_in == expected


async def test_complete_maps_exceptions_without_chaining(
    litellm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(**_: Any) -> Any:
        raise litellm.exceptions.RateLimitError(
            message=f"slow down {API_KEY}", llm_provider="groq", model="m", response=_response(429)
        )

    monkeypatch.setattr(litellm, "acompletion", failing)
    call = ChatCall(
        model="groq/x",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=5,
        timeout_s=1.0,
        api_key=API_KEY,
    )
    with pytest.raises(RateLimitedError) as excinfo:
        await LiteLLMProvider().complete(call)
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
    assert API_KEY not in str(excinfo.value)


def _groq_bad_request(litellm: Any, body: dict[str, Any]) -> Exception:
    exc: Exception = litellm.exceptions.BadRequestError(
        message=f"GroqException - {json.dumps(body)}",
        llm_provider="groq",
        model="m",
        response=_response(400),
    )
    return exc


REJECTION = {
    "error": {
        "message": "Failed to validate JSON.",
        "type": "invalid_request_error",
        "code": "json_validate_failed",
        "failed_generation": '{"total": "B.000"}',
    }
}


def _schema_call(schema: dict[str, Any] | None = None) -> ChatCall:
    return ChatCall(
        model="groq/x",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=5,
        timeout_s=1.0,
        json_schema=schema,
        api_key=API_KEY,
    )


async def test_provider_schema_rejection_is_returned_as_a_reply(
    litellm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rejecting(**_: Any) -> Any:
        raise _groq_bad_request(litellm, REJECTION)

    monkeypatch.setattr(litellm, "acompletion", rejecting)
    result = await LiteLLMProvider().complete(_schema_call({"type": "object"}))
    assert result.text == '{"total": "B.000"}'
    assert result.finish_reason == SCHEMA_REJECTED
    assert (result.tokens_in, result.tokens_out) == (None, None)


async def test_schema_rejection_without_generation_gives_empty_reply(
    litellm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"error": {**REJECTION["error"], "failed_generation": None}}

    async def rejecting(**_: Any) -> Any:
        raise _groq_bad_request(litellm, body)

    monkeypatch.setattr(litellm, "acompletion", rejecting)
    result = await LiteLLMProvider().complete(_schema_call({"type": "object"}))
    assert (result.text, result.finish_reason) == ("", SCHEMA_REJECTED)


@pytest.mark.parametrize(
    ("body", "schema"),
    [
        (REJECTION, None),  # no schema was requested: a real request error
        ({"error": {"code": "invalid_api_key", "message": "bad key"}}, {"type": "object"}),
        ({"error": "json_validate_failed"}, {"type": "object"}),
    ],
)
async def test_other_bad_requests_still_raise(
    litellm: Any,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    schema: dict[str, Any] | None,
) -> None:
    async def failing(**_: Any) -> Any:
        raise _groq_bad_request(litellm, body)

    monkeypatch.setattr(litellm, "acompletion", failing)
    with pytest.raises(ProviderRequestError):
        await LiteLLMProvider().complete(_schema_call(schema))


def test_schema_rejection_text_ignores_non_400_and_garbage(litellm: Any) -> None:
    server = litellm.exceptions.InternalServerError(
        message=json.dumps(REJECTION), llm_provider="groq", model="m"
    )
    assert schema_rejection_text(server) is None
    assert schema_rejection_text(ValueError("{not json")) is None
    assert schema_rejection_text(_groq_bad_request(litellm, REJECTION)) == '{"total": "B.000"}'


async def test_complete_rejects_empty_choices(
    litellm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(**_: Any) -> Any:
        return SimpleNamespace(choices=[], usage=None)

    monkeypatch.setattr(litellm, "acompletion", empty)
    call = ChatCall(model="groq/x", messages=[], temperature=0, max_tokens=1, timeout_s=1)
    with pytest.raises(ProviderRequestError, match="no choices"):
        await LiteLLMProvider().complete(call)


async def test_embed_parses_vectors(litellm: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_aembedding(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(
            data=[{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}],
            usage=SimpleNamespace(prompt_tokens=6),
        )

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    call = EmbedCall(
        model="ollama/e", inputs=["a", "b"], timeout_s=5, api_base="http://localhost:11434"
    )
    result = await LiteLLMProvider().embed(call)
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert result.tokens_in == 6
    assert seen["api_base"] == "http://localhost:11434" and "api_key" not in seen

    async def short(**_: Any) -> Any:
        return SimpleNamespace(data=[{"embedding": [0.1]}], usage=None)

    monkeypatch.setattr(litellm, "aembedding", short)
    with pytest.raises(ProviderRequestError, match="different number"):
        await LiteLLMProvider().embed(call)


# --- provider schema adaptation (M2.1: Gemini rejects length bounds) -----------------------


def test_gemini_schema_drops_length_bounds_everywhere_but_keeps_names_and_data() -> None:
    schema = {
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 300},
                    "maxLength": {"type": "integer", "default": {"maxLength": 3}},
                },
            }
        },
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}, "maxItems": 500},
            "tags": {"anyOf": [{"type": "array", "minItems": 1}, {"type": "null"}]},
            "currency": {"type": "string", "pattern": "^[A-Z]{3}$", "enum": ["IDR"]},
        },
    }
    sent = provider_schema("gemini/gemini-3.5-flash-lite", schema)

    text = json.dumps(sent)
    item = sent["$defs"]["Item"]["properties"]
    assert item["name"] == {"type": "string"}
    assert item["maxLength"] == {"type": "integer", "default": {"maxLength": 3}}  # name + data kept
    assert sent["properties"]["items"] == {"type": "array", "items": {"$ref": "#/$defs/Item"}}
    assert sent["properties"]["tags"]["anyOf"][0] == {"type": "array"}
    assert sent["properties"]["currency"]["pattern"] == "^[A-Z]{3}$"
    assert "maxItems" not in text and "minItems" not in text and "minLength" not in text
    assert schema["properties"]["items"]["maxItems"] == 500  # input not mutated


def test_receipt_schema_sent_to_gemini_has_no_length_bounds() -> None:
    from receipt_extractor.core.receipt import Receipt  # noqa: PLC0415

    full = Receipt.model_json_schema()
    sent = json.dumps(provider_schema("gemini/x", full))
    assert all(k not in sent for k in ("maxItems", "minLength", "maxLength"))
    assert provider_schema("groq/openai/gpt-oss-20b", full) is full  # other providers untouched


@pytest.mark.parametrize(("model", "stripped"), [("gemini/x", True), ("groq/x", False)])
async def test_complete_sends_provider_schema(
    litellm: Any, monkeypatch: pytest.MonkeyPatch, model: str, stripped: bool
) -> None:
    seen: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        message = SimpleNamespace(content="{}")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    schema = {"type": "array", "maxItems": 5}
    call = ChatCall(
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=50,
        timeout_s=9.0,
        json_schema=schema,
    )
    await LiteLLMProvider().complete(call)
    sent = seen["response_format"]["json_schema"]["schema"]
    assert ("maxItems" not in sent) is stripped


async def test_alias_options_are_passed_but_never_override_core_arguments(
    litellm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        message = SimpleNamespace(content="{}")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    call = ChatCall(
        model="ollama_chat/qwen3-vl:4b-instruct",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=50,
        timeout_s=600.0,
        api_base="http://localhost:11434",
        # The registry allow-list blocks these; the adapter must not let them win either.
        options={"num_ctx": 8192, "model": "groq/evil", "timeout": 1, "num_retries": 9},
    )
    await LiteLLMProvider().complete(call)

    assert seen["num_ctx"] == 8192
    assert seen["model"] == "ollama_chat/qwen3-vl:4b-instruct"
    assert seen["timeout"] == 600.0 and seen["num_retries"] == 0
    assert seen["api_base"] == "http://localhost:11434"


def test_ollama_schema_requires_every_property_but_keeps_nullability() -> None:
    from receipt_extractor.core.receipt import Receipt  # noqa: PLC0415

    full = Receipt.model_json_schema()
    sent = provider_schema("ollama_chat/qwen3-vl:4b-instruct", full)

    assert sent["required"] == ["items", "subtotal", "tax", "total", "currency"]
    item = sent["$defs"]["Item"]
    assert item["required"] == ["name", "qty", "unit_price", "price"]
    assert {"type": "null"} in item["properties"]["qty"]["anyOf"]  # null still allowed
    assert {"type": "null"} in sent["properties"]["tax"]["anyOf"]
    assert full["required"] == ["total"]  # input not mutated
    assert provider_schema("ollama/m", full)["required"] == sent["required"]


def test_require_all_properties_leaves_names_and_data_alone() -> None:
    schema = {
        "type": "object",
        "properties": {"required": {"type": "string", "default": {"properties": {"a": 1}}}},
    }
    sent = provider_schema("ollama/m", schema)
    assert sent["required"] == ["required"]
    assert sent["properties"]["required"]["default"] == {"properties": {"a": 1}}
    assert "required" not in sent["properties"]["required"]
