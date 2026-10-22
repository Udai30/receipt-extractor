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
