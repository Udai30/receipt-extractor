"""Scriptable in-memory provider for tests and offline runs (no network, deterministic).

A script is a sequence of steps consumed one per provider call; when it runs out the
``default`` step is used (or an error is raised if ``default`` is ``None``)::

    FakeProvider([FakeFailure("rate_limit", retry_after_s=2), FakeReply('{"a": 1}')])

``FakeFailure("hang", latency_s=...)`` sleeps for real so the client's hard timeout fires.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from receipt_extractor.llm.errors import (
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)
from receipt_extractor.llm.providers import ChatCall, ChatResult, EmbedCall, EmbedResult

FailureKind = Literal["rate_limit", "server_error", "timeout", "bad_request", "hang"]


@dataclass(frozen=True, slots=True)
class FakeReply:
    text: str = '{"ok": true}'
    tokens_in: int | None = 10
    tokens_out: int | None = 5
    latency_s: float = 0.0
    finish_reason: str = "stop"


@dataclass(frozen=True, slots=True)
class FakeFailure:
    kind: FailureKind
    retry_after_s: float | None = None
    latency_s: float = 0.0


FakeStep = FakeReply | FakeFailure
_DEFAULT_REPLY = FakeReply()


class FakeProvider:
    """Implements :class:`~receipt_extractor.llm.providers.Provider` from a script."""

    def __init__(
        self,
        script: Iterable[FakeStep] = (),
        *,
        default: FakeStep | None = _DEFAULT_REPLY,
        embed_dim: int = 8,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._script: deque[FakeStep] = deque(script)
        self._default = default
        self._embed_dim = embed_dim
        self._sleep = sleep
        self.calls: list[ChatCall] = []
        self.embed_calls: list[EmbedCall] = []

    def push(self, *steps: FakeStep) -> None:
        self._script.extend(steps)

    def _next(self) -> FakeStep:
        if self._script:
            return self._script.popleft()
        if self._default is None:
            raise AssertionError("FakeProvider script exhausted")
        return self._default

    async def _play(self, step: FakeStep) -> FakeReply:
        if step.latency_s:
            await self._sleep(step.latency_s)
        if isinstance(step, FakeReply):
            return step
        match step.kind:
            case "rate_limit":
                raise RateLimitedError(
                    "429 Too Many Requests (fake)",
                    status_code=429,
                    retry_after_s=step.retry_after_s,
                )
            case "server_error":
                raise ProviderUnavailableError("503 Service Unavailable (fake)", status_code=503)
            case "timeout":
                raise ProviderTimeoutError("request timed out (fake)")
            case "bad_request":
                raise ProviderRequestError("400 Bad Request (fake)", status_code=400)
            case "hang":
                await asyncio.sleep(3600)  # cancelled by the client's timeout
                raise AssertionError("unreachable: hang was not cancelled")

    async def complete(self, call: ChatCall) -> ChatResult:
        self.calls.append(call)
        reply = await self._play(self._next())
        return ChatResult(
            text=reply.text,
            tokens_in=reply.tokens_in,
            tokens_out=reply.tokens_out,
            finish_reason=reply.finish_reason,
        )

    async def embed(self, call: EmbedCall) -> EmbedResult:
        self.embed_calls.append(call)
        reply = await self._play(self._next())
        vectors = [self._vector(text) for text in call.inputs]
        return EmbedResult(vectors=vectors, tokens_in=reply.tokens_in)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self._embed_dim)]
