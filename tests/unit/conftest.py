"""Fixtures for the LLM-layer unit tests: an in-memory registry, a fake clock and client factory.

Everything runs offline against :class:`FakeProvider`; time is simulated, so backoff and
rate-limit waits take no wall-clock time.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.llm.cache import DiskCache
from receipt_extractor.llm.client import Credentials, LLMClient
from receipt_extractor.llm.fake import FakeProvider
from receipt_extractor.llm.limits import BudgetTracker
from receipt_extractor.llm.registry import (
    ModelRegistry,
    ModelSpec,
    PriceEntry,
    PriceTable,
    RateLimits,
)
from receipt_extractor.llm.retry import RetryPolicy
from receipt_extractor.llm.sinks import MemorySink

TODAY = date(2026, 10, 19)
GROQ_TEST_KEY = "gsk_TESTKEYtestkey0123456789"  # fake value, never a real credential


class FakeClock:
    """Monotonic clock whose ``sleep`` advances time instantly and records each wait."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_registry() -> ModelRegistry:
    specs = [
        ModelSpec(alias="test-chat", provider="groq", kind="chat", model="groq/test-model"),
        ModelSpec(
            alias="test-public",
            provider="gemini",
            kind="chat",
            model="gemini/test-flash",
            data_policy="public_only",
        ),
        ModelSpec(
            alias="test-embed", provider="ollama", kind="embedding", model="ollama/test-embed"
        ),
        ModelSpec(
            alias="test-limited",
            provider="groq",
            kind="chat",
            model="groq/test-model",
            limits=RateLimits(rpm=2, tpm=1000, rpd=3, tpd=100_000),
        ),
        ModelSpec(alias="test-local", provider="local", kind="local", model="org/local-model"),
    ]
    return ModelRegistry({s.alias: s for s in specs})


def make_pricing() -> PriceTable:
    def entry(inp: str, out: str) -> PriceEntry:
        return PriceEntry(
            effective_from=date(2026, 1, 1),
            input_per_mtok=Decimal(inp),
            output_per_mtok=Decimal(out),
            source="test",
            checked=date(2026, 1, 1),
        )

    return PriceTable(
        {
            "groq/test-model": [entry("1.00", "2.00")],
            "gemini/test-flash": [entry("0.50", "1.50")],
            "ollama/test-embed": [entry("0", "0")],
        }
    )


ClientFactory = Callable[..., LLMClient]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def make_client(clock: FakeClock, sink: MemorySink, tmp_path: Path) -> ClientFactory:
    """Build an :class:`LLMClient` over ``provider`` with fake time and an in-memory sink."""

    def factory(
        provider: FakeProvider,
        *,
        cache: bool = False,
        budget: bool = False,
        credentials: Credentials | None = None,
        **overrides: Any,
    ) -> LLMClient:
        options: dict[str, Any] = {
            "registry": make_registry(),
            "pricing": make_pricing(),
            "provider": provider,
            "credentials": credentials
            or Credentials(
                groq_api_key=GROQ_TEST_KEY,
                gemini_api_key="AIzaTESTtestTESTtest0123456789",
                ollama_base_url="http://localhost:11434",
            ),
            "cache": DiskCache(tmp_path / "cache") if cache else None,
            "budget": BudgetTracker(tmp_path / "budget.json", today=lambda: TODAY)
            if budget
            else None,
            "sinks": [sink],
            "retry": RetryPolicy(max_attempts=5, base_delay_s=1.0, max_delay_s=30.0),
            "clock": clock,
            "sleep": clock.sleep,
            "rng": random.Random(0),  # noqa: S311 - deterministic jitter
            "today": lambda: TODAY,
        }
        options.update(overrides)
        return LLMClient(**options)

    return factory
