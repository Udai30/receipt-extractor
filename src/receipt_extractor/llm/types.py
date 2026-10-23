"""Request/response contracts of the LLM layer (TRD §S3) and the ``llm_call`` record.

``LLMCallRecord`` is BACKEND_SCHEMA §S2 ``LLMCallRecord``: what sinks persist as one line of
``runs/<run_id>/llm_calls.jsonl`` (or keep in memory).
Records never contain prompts, outputs or API keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_IMAGE_BYTES: Final = 8 * 1024 * 1024
MAX_TEXT_CHARS: Final = 400_000

DataOrigin = Literal["public_dataset", "synthetic", "user_upload"]
"""Where the content of a request comes from.

``public_dataset`` — CORD or other public data; ``synthetic`` — text authored by this project
(prompts, pings); ``user_upload`` — anything a user sent. The default is the most restrictive.
"""

_FROZEN = ConfigDict(extra="forbid", frozen=True)
_FENCE_RE: Final = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)


class TextPart(BaseModel):
    model_config = _FROZEN

    type: Literal["text"] = "text"
    text: str = Field(max_length=MAX_TEXT_CHARS)


class ImagePart(BaseModel):
    """An inline image (sent as a base64 data URL; never written to disk by this layer)."""

    model_config = _FROZEN

    type: Literal["image"] = "image"
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: bytes = Field(min_length=1, max_length=MAX_IMAGE_BYTES, repr=False)

    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{base64.b64encode(self.data).decode('ascii')}"


ContentPart = Annotated[TextPart | ImagePart, Field(discriminator="type")]


class Message(BaseModel):
    model_config = _FROZEN

    role: Literal["system", "user", "assistant"]
    content: str | tuple[ContentPart, ...] = Field(min_length=1)

    def to_provider(self) -> dict[str, Any]:
        """OpenAI-style message dict understood by LiteLLM for every provider."""
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        parts: list[dict[str, Any]] = []
        for part in self.content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            else:
                parts.append({"type": "image_url", "image_url": {"url": part.data_url()}})
        return {"role": self.role, "content": parts}

    def cache_repr(self) -> dict[str, Any]:
        """Stable, compact representation for cache keys (images hashed by content)."""
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        parts: list[dict[str, Any]] = []
        for part in self.content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            else:
                digest = hashlib.sha256(part.data).hexdigest()
                parts.append({"type": "image", "mime": part.mime_type, "sha256": digest})
        return {"role": self.role, "content": parts}

    def text_chars(self) -> int:
        if isinstance(self.content, str):
            return len(self.content)
        return sum(len(p.text) for p in self.content if isinstance(p, TextPart))

    def image_count(self) -> int:
        if isinstance(self.content, str):
            return 0
        return sum(1 for p in self.content if isinstance(p, ImagePart))


class CallTags(BaseModel):
    """Correlation fields copied onto every ``llm_call`` record (not part of the cache key)."""

    model_config = _FROZEN

    run_id: uuid.UUID | None = None
    request_id: str | None = Field(default=None, max_length=128)
    case_id: str | None = Field(default=None, max_length=256)
    stage: str | None = Field(default=None, max_length=64)


class LLMRequest(BaseModel):
    """A chat completion request against a model alias.

    ``response_model`` turns on structured output: its JSON schema is sent as the
    ``response_format`` and the reply is validated against it; an invalid reply is sent back
    with its validation errors up to ``max_repairs`` times, unless ``repair`` is ``False`` —
    naive baselines make exactly one call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    model_alias: str = Field(min_length=1, max_length=64)
    messages: tuple[Message, ...] = Field(min_length=1, max_length=64)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=65536)
    seed: int | None = None
    response_model: type[BaseModel] | None = Field(default=None, exclude=True)
    repair: bool = True
    max_repairs: int = Field(default=1, ge=1, le=2)
    data_origin: DataOrigin = "user_upload"
    tags: CallTags = CallTags()
    timeout_s: float | None = Field(default=None, gt=0, le=600)


class EmbedRequest(BaseModel):
    """An embedding request against an embedding alias."""

    model_config = _FROZEN

    model_alias: str = Field(min_length=1, max_length=64)
    inputs: tuple[Annotated[str, Field(min_length=1, max_length=32_000)], ...] = Field(
        min_length=1, max_length=256
    )
    data_origin: DataOrigin = "user_upload"
    tags: CallTags = CallTags()
    timeout_s: float | None = Field(default=None, gt=0, le=600)


def extract_json_text(text: str) -> str:
    """Strip whitespace and a surrounding Markdown code fence, if the model added one."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


class _Accounting(BaseModel):
    model_config = _FROZEN

    model_alias: str
    provider: str
    model: str
    latency_ms: int = Field(ge=0)
    wait_ms: int = Field(default=0, ge=0)
    cache_hit: bool
    retries: int = Field(ge=0)
    list_cost_usd: Decimal | None
    actual_cost_usd: Decimal
    call_ids: tuple[uuid.UUID, ...]


class LLMResponse(_Accounting):
    """Result of :meth:`LLMClient.complete`, aggregated over the call and any repair call."""

    text: str
    tokens_in: int | None
    tokens_out: int | None
    image_tokens_in: int | None = None  # the part of tokens_in spent on images, if reported
    finish_reason: str | None = None
    schema_valid: bool | None
    repaired: bool = False
    attempts: int = Field(default=1, ge=1)  # provider calls behind this reply (incl. repairs)

    def parse[T: BaseModel](self, model: type[T]) -> T:
        """Validate the reply into ``model`` (raises ``ValueError`` when it does not fit)."""
        try:
            return model.model_validate_json(extract_json_text(self.text))
        except ValidationError as exc:
            raise ValueError(
                f"reply does not match {model.__name__}: {exc.error_count()} error(s)"
            ) from None

    def json_value(self) -> Any:
        """The reply decoded as JSON (raises ``ValueError`` when it is not JSON)."""
        return json.loads(extract_json_text(self.text))


class EmbedResponse(_Accounting):
    """Result of :meth:`LLMClient.embed`."""

    vectors: tuple[tuple[float, ...], ...]
    tokens_in: int | None


class LLMCallRecord(BaseModel):
    """One provider call (or cache hit) — one line of ``llm_calls.jsonl`` (BACKEND_SCHEMA §S2)."""

    model_config = _FROZEN

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: uuid.UUID | None = None
    request_id: str | None = None
    case_id: str | None = None
    stage: str | None = None
    model_alias: str
    provider: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    image_tokens_in: int | None = None
    latency_ms: int = Field(ge=0)
    wait_ms: int = Field(default=0, ge=0)
    cache_hit: bool
    retries: int = Field(default=0, ge=0, le=32767)
    schema_valid: bool | None = None
    list_cost_usd: Decimal | None = None
    actual_cost_usd: Decimal = Decimal(0)
    error: str | None = None
