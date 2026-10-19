"""Per-alias rate limiting (RPM/TPM token buckets) and daily budgets (RPD/TPD) — TRD §S3.

The limiter never busy-waits: it computes how long until enough capacity refills and sleeps
exactly that long while holding a FIFO ``asyncio.Lock``, so waiting callers queue fairly and
do no work. Token counts are reserved from an estimate before the call and settled with the
real usage afterwards.

The budget tracker persists today's usage (UTC day) in a small JSON state file so separate
CLI invocations on the same day share it, and raises :class:`BudgetExhaustedError` *before*
a call that would exceed the day's limit. It is safe for concurrent tasks in one process; it
is not a cross-process lock — run one sweep per alias at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from receipt_extractor.llm.errors import BudgetExhaustedError
from receipt_extractor.llm.registry import RateLimits

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
_SECONDS_PER_MINUTE = 60.0


def utc_today() -> date:
    return datetime.now(UTC).date()


class TokenBucket:
    """Classic token bucket: ``capacity`` tokens, refilled continuously at ``rate`` per second.

    The balance may go negative after :meth:`adjust` records more usage than was reserved;
    later callers then wait for the debt to refill.
    """

    def __init__(self, capacity: float, rate_per_s: float, now: float) -> None:
        if capacity <= 0 or rate_per_s <= 0:
            raise ValueError("capacity and rate must be positive")
        self.capacity = capacity
        self.rate = rate_per_s
        self.tokens = capacity
        self._updated = now

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._updated = now

    def wait_time(self, amount: float, now: float) -> float:
        """Seconds until ``amount`` tokens are available (0 when available now)."""
        self._refill(now)
        deficit = amount - self.tokens
        return 0.0 if deficit <= 0 else deficit / self.rate

    def take(self, amount: float, now: float) -> None:
        self._refill(now)
        self.tokens -= amount

    def adjust(self, delta: float, now: float) -> None:
        """Consume ``delta`` more tokens (negative ``delta`` gives tokens back)."""
        self._refill(now)
        self.tokens = min(self.capacity, self.tokens - delta)


class RateLimiter:
    """RPM + TPM limiter of one alias."""

    def __init__(
        self, limits: RateLimits, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        now = clock()
        self._requests = (
            TokenBucket(limits.rpm, limits.rpm / _SECONDS_PER_MINUTE, now) if limits.rpm else None
        )
        self._tokens = (
            TokenBucket(limits.tpm, limits.tpm / _SECONDS_PER_MINUTE, now) if limits.tpm else None
        )

    async def acquire(self, tokens: int) -> int:
        """Wait until one request and ``tokens`` tokens fit; return the tokens reserved.

        A single request larger than the per-minute capacity is clamped to the capacity
        (it would otherwise wait forever); the provider decides whether it is acceptable.
        """
        reserve = tokens if self._tokens is None else min(tokens, int(self._tokens.capacity))
        async with self._lock:
            while True:
                now = self._clock()
                wait = max(
                    self._requests.wait_time(1, now) if self._requests else 0.0,
                    self._tokens.wait_time(reserve, now) if self._tokens else 0.0,
                )
                if wait <= 0:
                    if self._requests:
                        self._requests.take(1, now)
                    if self._tokens:
                        self._tokens.take(reserve, now)
                    return reserve
                await self._sleep(wait)

    def settle(self, reserved: int, actual: int) -> None:
        """Correct the token bucket once the real usage of a call is known."""
        if self._tokens is not None and actual != reserved:
            self._tokens.adjust(actual - reserved, self._clock())


class BudgetTracker:
    """Daily request/token usage per alias, persisted to ``state_path``."""

    def __init__(self, state_path: Path, *, today: Callable[[], date] = utc_today) -> None:
        self._path = state_path
        self._today = today
        self._lock = asyncio.Lock()
        self._loaded = False
        self._day = ""
        self._usage: dict[str, dict[str, int]] = {}

    async def reserve(self, alias: str, limits: RateLimits, tokens: int) -> None:
        """Count one request and ``tokens`` estimated tokens, or raise if over budget."""
        async with self._lock:
            await self._ensure_current()
            usage = self._usage.setdefault(alias, {"requests": 0, "tokens": 0})
            if limits.rpd is not None and usage["requests"] + 1 > limits.rpd:
                raise BudgetExhaustedError(alias, "request", usage["requests"], limits.rpd)
            if limits.tpd is not None and usage["tokens"] + tokens > limits.tpd:
                raise BudgetExhaustedError(alias, "token", usage["tokens"], limits.tpd)
            usage["requests"] += 1
            usage["tokens"] += tokens
            await self._persist()

    async def settle(self, alias: str, reserved: int, actual: int) -> None:
        """Replace the reserved token estimate with the actual usage."""
        if reserved == actual:
            return
        async with self._lock:
            await self._ensure_current()
            usage = self._usage.setdefault(alias, {"requests": 0, "tokens": 0})
            usage["tokens"] = max(0, usage["tokens"] + actual - reserved)
            await self._persist()

    async def usage(self, alias: str) -> dict[str, int]:
        async with self._lock:
            await self._ensure_current()
            return dict(self._usage.get(alias, {"requests": 0, "tokens": 0}))

    async def _ensure_current(self) -> None:
        today = self._today().isoformat()
        if not self._loaded:
            state = await asyncio.to_thread(self._read)
            self._loaded = True
            if state is not None and state.get("date") == today:
                self._usage = state["usage"]
        if self._day != today:
            if self._day:
                self._usage = {}
            self._day = today

    def _read(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("llm budget state unreadable; starting today's count from zero")
            return None
        usage = raw.get("usage") if isinstance(raw, dict) else None
        if not isinstance(usage, dict) or not all(
            isinstance(v, dict)
            and isinstance(v.get("requests"), int)
            and isinstance(v.get("tokens"), int)
            for v in usage.values()
        ):
            logger.warning("llm budget state malformed; starting today's count from zero")
            return None
        return {"date": raw.get("date"), "usage": {k: dict(v) for k, v in usage.items()}}

    async def _persist(self) -> None:
        payload = json.dumps({"date": self._day, "usage": self._usage}, sort_keys=True)
        await asyncio.to_thread(self._write, payload)

    def _write(self, payload: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".tmp-budget-")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            tmp_path.replace(self._path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
