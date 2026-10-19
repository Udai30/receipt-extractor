"""Retry policy: exponential backoff with jitter, honouring ``Retry-After`` (TRD §S3)."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """``max_attempts`` counts the first call; ``max_retry_after_s`` caps how long a server
    may ask us to wait before we give up instead (e.g. a daily quota reset hours away)."""

    max_attempts: int = 5
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    max_retry_after_s: float = 120.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_delay_s < 0 or self.max_delay_s < self.base_delay_s:
            raise ValueError("invalid retry policy")

    def delay(self, failures: int, retry_after_s: float | None, rng: random.Random) -> float:
        """Delay before the next attempt after ``failures`` consecutive failures.

        Equal jitter: half the exponential step is fixed, half is random, so concurrent
        callers spread out but never retry immediately. A server ``Retry-After`` is a floor.
        """
        step = min(self.max_delay_s, self.base_delay_s * 2.0 ** max(0, failures - 1))
        jittered = step / 2 + rng.uniform(0, step / 2)
        if retry_after_s is not None:
            return max(retry_after_s, jittered)
        return jittered


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) into seconds ≥ 0."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)
