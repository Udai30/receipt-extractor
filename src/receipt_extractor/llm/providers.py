"""Provider protocol and the LiteLLM adapter (TRD §S1: LiteLLM is the single provider interface).

Adapters make exactly one attempt per call and translate failures into the
:mod:`receipt_extractor.llm.errors` hierarchy; retries, timeouts, limits and accounting are the
client's job. LiteLLM is imported lazily (it takes seconds to import) with its remote model-cost
map disabled, so importing this module never touches the network.

A structured-output request whose reply the *provider* rejects against the schema (Groq:
HTTP 400 ``json_validate_failed``) is not a request error: the model answered, just not in
shape. It is returned as a reply (the rejected text, ``finish_reason = "schema_rejected"``)
so the client's own validation/repair handles it like any other invalid reply.

Gemini rejects the whole request (HTTP 400 ``INVALID_ARGUMENT``) when the response schema carries
length bounds (``maxItems``, ``minLength``, ``maxLength``; found in M2.1). They are stripped from
the schema *sent* to ``gemini/`` models; the client still validates every reply against the full
Pydantic model, so the bounds are enforced all the same.

Ollama decodes against the schema as a grammar, and a small model then writes only the
*required* keys (``Receipt``: just ``total``) and stops — no items (M2.1, Qwen3-VL-4B). For
``ollama/``/``ollama_chat/`` models every object property is marked required in the schema
sent; optional fields stay nullable, so the model writes ``null`` for what is not printed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Protocol

from receipt_extractor.llm.errors import (
    ProviderError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
    sanitize_error_text,
)
from receipt_extractor.llm.retry import parse_retry_after

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_REQUEST_TIMEOUT = 408
_HTTP_SERVER_ERROR = 500
_HTTP_BAD_REQUEST = 400
SCHEMA_REJECTED = "schema_rejected"
_SCHEMA_REJECTION_CODES = frozenset({"json_validate_failed"})
_MAX_REJECTED_TEXT_CHARS = 64_000
_GEMINI_UNSUPPORTED_KEYWORDS = frozenset({"maxItems", "minItems", "minLength", "maxLength"})
_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
_SCHEMA_VALUES = frozenset({"default", "enum", "const", "examples"})  # data, not schemas


@dataclass(frozen=True, slots=True)
class ChatCall:
    """One provider chat request. ``api_key`` is excluded from ``repr``."""

    model: str
    messages: list[dict[str, Any]]
    temperature: float
    max_tokens: int
    timeout_s: float
    seed: int | None = None
    json_schema: dict[str, Any] | None = None
    schema_name: str = "response"
    api_key: str | None = field(default=None, repr=False)
    api_base: str | None = None
    options: dict[str, Any] = field(default_factory=dict)  # alias options (registry allow-list)


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    tokens_in: int | None
    tokens_out: int | None
    finish_reason: str | None = None
    image_tokens_in: int | None = None  # part of tokens_in, when the provider reports it


@dataclass(frozen=True, slots=True)
class EmbedCall:
    model: str
    inputs: list[str]
    timeout_s: float
    api_key: str | None = field(default=None, repr=False)
    api_base: str | None = None


@dataclass(frozen=True, slots=True)
class EmbedResult:
    vectors: list[list[float]]
    tokens_in: int | None


class Provider(Protocol):
    """What the client needs from a backend (LiteLLM in production, a fake in tests)."""

    async def complete(self, call: ChatCall) -> ChatResult: ...

    async def embed(self, call: EmbedCall) -> EmbedResult: ...


def _litellm() -> ModuleType:
    # Use the bundled cost map instead of fetching it from GitHub on import.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm  # noqa: PLC0415 - deliberately lazy (slow import)

    litellm.suppress_debug_info = True
    litellm.drop_params = True  # e.g. `seed` on providers that do not support it
    if hasattr(litellm, "telemetry"):
        litellm.telemetry = False
    return litellm


def _header(exc: BaseException, name: str) -> str | None:
    for source in (
        getattr(exc, "litellm_response_headers", None),
        getattr(exc, "headers", None),
        getattr(getattr(exc, "response", None), "headers", None),
    ):
        if source is None:
            continue
        try:
            value = source.get(name)
        except AttributeError:
            continue
        if isinstance(value, str):
            return value
    return None


def map_provider_exception(exc: BaseException, *, secrets: list[str]) -> ProviderError:
    """Translate a LiteLLM/httpx exception into a sanitised :class:`ProviderError`."""
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    name = type(exc).__name__
    message = sanitize_error_text(f"{name}: {exc}", secrets)
    retry_after = parse_retry_after(_header(exc, "retry-after"))

    if name in {"Timeout", "APITimeoutError", "TimeoutException", "ReadTimeout"} or isinstance(
        exc, TimeoutError
    ):
        return ProviderTimeoutError(message, status_code=status)
    if name == "RateLimitError" or status == _HTTP_TOO_MANY_REQUESTS:
        return RateLimitedError(message, status_code=status, retry_after_s=retry_after)
    if name in {
        "APIConnectionError",
        "InternalServerError",
        "ServiceUnavailableError",
        "BadGatewayError",
        "ConnectError",
    } or (status is not None and (status >= _HTTP_SERVER_ERROR or status == _HTTP_REQUEST_TIMEOUT)):
        return ProviderUnavailableError(message, status_code=status, retry_after_s=retry_after)
    return ProviderRequestError(message, status_code=status)


def schema_rejection_text(exc: BaseException) -> str | None:
    """The rejected generation if ``exc`` is a provider-side schema rejection, else ``None``.

    Only a 400 whose JSON error body carries a known code counts; the generation is model
    output (empty when the provider omits it), never an echo of the request.
    """
    if getattr(exc, "status_code", None) != _HTTP_BAD_REQUEST:
        return None
    text = str(exc)
    start = text.find("{")
    if start < 0:
        return None
    try:
        body, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict) or error.get("code") not in _SCHEMA_REJECTION_CODES:
        return None
    generation = error.get("failed_generation")
    return generation[:_MAX_REJECTED_TEXT_CHARS] if isinstance(generation, str) else ""


def strip_schema_keywords(schema: Any, keywords: frozenset[str]) -> Any:
    """A copy of JSON ``schema`` without ``keywords`` anywhere a sub-schema can appear.

    Property names (keys of ``properties``/``$defs``) and data values (``default``, ``enum``, …)
    are left alone, so a field that happens to be called ``maxLength`` survives.
    """
    if isinstance(schema, list):
        return [strip_schema_keywords(item, keywords) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in keywords:
            continue
        if key in _SCHEMA_VALUES:
            out[key] = value
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: strip_schema_keywords(sub, keywords) for name, sub in value.items()}
        else:
            out[key] = strip_schema_keywords(value, keywords)
    return out


def require_all_properties(schema: Any) -> Any:
    """A copy of JSON ``schema`` where every object sub-schema lists all its properties as required.

    Walks sub-schemas the same way as :func:`strip_schema_keywords` (property names and data
    values are not schemas).
    """
    if isinstance(schema, list):
        return [require_all_properties(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _SCHEMA_VALUES:
            out[key] = value
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: require_all_properties(sub) for name, sub in value.items()}
        else:
            out[key] = require_all_properties(value)
    properties = out.get("properties")
    if isinstance(properties, dict) and properties:
        out["required"] = list(properties)
    return out


def provider_schema(model: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The response schema as ``model``'s provider accepts it (see the module docstring)."""
    if model.startswith("gemini/"):
        stripped: dict[str, Any] = strip_schema_keywords(schema, _GEMINI_UNSUPPORTED_KEYWORDS)
        return stripped
    if model.startswith(("ollama/", "ollama_chat/")):
        required: dict[str, Any] = require_all_properties(schema)
        return required
    return schema


def _usage(response: Any, key: str) -> int | None:
    usage = getattr(response, "usage", None)
    value = getattr(usage, key, None) if usage is not None else None
    return value if isinstance(value, int) and value >= 0 else None


def _image_tokens_in(response: Any) -> int | None:
    """Input tokens spent on images (Gemini reports tokens per modality), if reported."""
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
    value = getattr(details, "image_tokens", None) if details is not None else None
    return value if isinstance(value, int) and value >= 0 else None


class LiteLLMProvider:
    """Calls Groq / Gemini / Ollama through ``litellm.acompletion`` / ``aembedding``."""

    async def complete(self, call: ChatCall) -> ChatResult:
        litellm = _litellm()
        kwargs: dict[str, Any] = {
            **call.options,  # first, so the arguments below always win
            "model": call.model,
            "messages": call.messages,
            "temperature": call.temperature,
            "max_tokens": call.max_tokens,
            "timeout": call.timeout_s,
            "num_retries": 0,  # the client owns retries
        }
        if call.seed is not None:
            kwargs["seed"] = call.seed
        if call.api_key is not None:
            kwargs["api_key"] = call.api_key
        if call.api_base is not None:
            kwargs["api_base"] = call.api_base
        if call.json_schema is not None:
            # Not `strict`: Pydantic schemas rarely meet strict-mode rules; the client validates.
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": call.schema_name,
                    "schema": provider_schema(call.model, call.json_schema),
                },
            }
        secrets = [call.api_key] if call.api_key else []
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            rejected = schema_rejection_text(exc) if call.json_schema is not None else None
            if rejected is not None:
                return ChatResult(
                    text=rejected, tokens_in=None, tokens_out=None, finish_reason=SCHEMA_REJECTED
                )
            raise map_provider_exception(exc, secrets=secrets) from None
        try:
            choice = response.choices[0]
            text = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
        except (AttributeError, IndexError, TypeError):
            raise ProviderRequestError("provider returned no choices") from None
        return ChatResult(
            text=text,
            tokens_in=_usage(response, "prompt_tokens"),
            tokens_out=_usage(response, "completion_tokens"),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            image_tokens_in=_image_tokens_in(response),
        )

    async def embed(self, call: EmbedCall) -> EmbedResult:
        litellm = _litellm()
        kwargs: dict[str, Any] = {
            "model": call.model,
            "input": call.inputs,
            "timeout": call.timeout_s,
            "num_retries": 0,
        }
        if call.api_key is not None:
            kwargs["api_key"] = call.api_key
        if call.api_base is not None:
            kwargs["api_base"] = call.api_base
        secrets = [call.api_key] if call.api_key else []
        try:
            response = await litellm.aembedding(**kwargs)
        except Exception as exc:
            raise map_provider_exception(exc, secrets=secrets) from None
        try:
            vectors = [[float(x) for x in item["embedding"]] for item in response.data]
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ProviderRequestError("provider returned malformed embeddings") from None
        if len(vectors) != len(call.inputs):
            raise ProviderRequestError("provider returned a different number of embeddings")
        return EmbedResult(vectors=vectors, tokens_in=_usage(response, "prompt_tokens"))
