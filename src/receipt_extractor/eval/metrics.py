"""Metric registry and the statistics behind ``summary.json`` (TRD §S4, §S5).

A metric scores one case: ``compute(prediction, gold) -> float | None`` on the JSON forms that
``results.jsonl`` stores (so run-time and imported predictions are scored identically).
``None`` means "not applicable to this case" and is left out of the mean. Project metrics
(item F1, valid JSON %, … in M1.2) register themselves with :func:`register_metric`.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from receipt_extractor.eval.errors import UnknownMetricError
from receipt_extractor.eval.io import canonical_json


class Metric(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def bootstrap_ci(self) -> bool:
        """Whether a bootstrap CI of the mean is meaningful for this metric."""
        ...

    def compute(self, prediction: Any, gold: Any) -> float | None: ...


_REGISTRY: dict[str, Metric] = {}


def register_metric(metric: Metric, *, replace: bool = False) -> Metric:
    if metric.name in _REGISTRY and not replace:
        raise ValueError(f"metric {metric.name!r} is already registered")
    _REGISTRY[metric.name] = metric
    return metric


def get_metric(name: str) -> Metric:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise UnknownMetricError(f"unknown metric {name!r} (registered: {known})") from None


def metric_names() -> list[str]:
    return sorted(_REGISTRY)


@dataclass(frozen=True)
class ExactMatch:
    """1.0 when prediction and gold are equal as canonical JSON, else 0.0."""

    name: str = "exact_match"
    bootstrap_ci: bool = True

    def compute(self, prediction: Any, gold: Any) -> float | None:
        return 1.0 if canonical_json(prediction) == canonical_json(gold) else 0.0


register_metric(ExactMatch())


def mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def quantile(sorted_values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation quantile of already sorted values (numpy's default method)."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    fraction = position - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * fraction


def bootstrap_ci(
    values: Sequence[float], *, resamples: int, confidence: float, seed: str | int
) -> tuple[float, float] | None:
    """Percentile bootstrap CI of the mean; identical output for identical ``seed``."""
    n = len(values)
    if n == 0:
        return None
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not cryptography
    means = sorted(math.fsum(rng.choices(values, k=n)) / n for _ in range(resamples))
    alpha = (1.0 - confidence) / 2.0
    low = quantile(means, alpha)
    high = quantile(means, 1.0 - alpha)
    if low is None or high is None:  # pragma: no cover - means has `resamples` >= 1 entries
        return None
    return low, high
