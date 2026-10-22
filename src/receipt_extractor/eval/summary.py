"""``summary.json`` and ``manifest.json`` builders (TRD §S4, §S5; BACKEND_SCHEMA §S2).

The summary is computed by streaming ``results.jsonl``. It keeps one compact record per case
(metric values, latency, tokens, cost) because bootstrap CIs and percentiles need every value;
predictions and gold are never held. If a case id appears twice, the last line wins.

Error rows count as 0.0 for every metric: an exception is a wrong answer, and leaving errors
out would flatter a variant that fails often. ``n_errors`` is reported alongside.
"""

from __future__ import annotations

import math
import platform
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from receipt_extractor.eval.config import RunConfig
from receipt_extractor.eval.errors import RunCorruptError
from receipt_extractor.eval.io import iter_jsonl
from receipt_extractor.eval.metrics import bootstrap_ci, get_metric, mean, quantile
from receipt_extractor.eval.records import (
    CaseStatus,
    DatasetInfo,
    KaggleInfo,
    LatencySummary,
    Manifest,
    MetricValue,
    ResultRow,
    Summary,
    TokenSummary,
)

P99_MIN_CASES: Final = 500
_PACKAGES: Final = ("receipt-extractor", "pydantic", "litellm", "pyyaml", "typer", "matplotlib")
_GIT_SHA_LEN: Final = 40
_MAX_GIT_FILE_BYTES: Final = 1024 * 1024


@dataclass(frozen=True)
class _CaseStats:
    status: CaseStatus
    metrics: Mapping[str, float | None]
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost: Decimal


def _collect(results_path: Path) -> dict[str, _CaseStats]:
    cases: dict[str, _CaseStats] = {}
    if not results_path.is_file():
        return cases
    for lineno, obj in iter_jsonl(results_path):
        try:
            row = ResultRow.model_validate(obj)
        except ValidationError:
            raise RunCorruptError(f"{results_path.name}:{lineno}: not a valid result row") from None
        raw = row.details.get("metrics", {})
        values = {
            str(k): (float(v) if isinstance(v, int | float) and math.isfinite(v) else None)
            for k, v in (raw.items() if isinstance(raw, dict) else ())
        }
        cases[row.case_id] = _CaseStats(
            status=row.status,
            metrics=values,
            latency_ms=row.latency_ms,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            cost=row.list_cost_usd,
        )
    return cases


def build_summary(
    results_path: Path,
    *,
    run_id: str,
    config: RunConfig,
    dataset_name: str,
    started_at: datetime,
    finished_at: datetime,
) -> Summary:
    cases = _collect(results_path)
    stats = list(cases.values())
    metrics: dict[str, MetricValue] = {}
    for name in config.metrics:
        values: list[float] = []
        for case in stats:
            if case.status == "error":
                values.append(0.0)
            elif (value := case.metrics.get(name)) is not None:
                values.append(value)
        ci = None
        if get_metric(name).bootstrap_ci:
            ci = bootstrap_ci(
                values,
                resamples=config.bootstrap.resamples,
                confidence=config.bootstrap.confidence,
                seed=f"{config.seed}:{name}",
            )
        metrics[name] = MetricValue(
            value=mean(values),
            ci_low=ci[0] if ci else None,
            ci_high=ci[1] if ci else None,
            n=len(values),
        )

    latencies = sorted(float(case.latency_ms) for case in stats)
    counts: dict[CaseStatus, int] = {"pass": 0, "fail": 0, "error": 0}
    for case in stats:
        counts[case.status] += 1
    total_cost = sum((case.cost for case in stats), Decimal(0))
    return Summary(
        run_id=run_id,
        variant=config.variant,
        dataset_name=dataset_name,
        dataset_split=config.dataset.split,
        sample_size=len(stats),
        primary_metric=config.primary_metric,
        metrics=metrics,
        status_counts=counts,
        latency_ms=LatencySummary(
            p50=quantile(latencies, 0.50),
            p95=quantile(latencies, 0.95),
            p99=quantile(latencies, 0.99) if len(latencies) >= P99_MIN_CASES else None,
        ),
        tokens=TokenSummary.model_validate(
            {
                "in": sum(case.tokens_in for case in stats),
                "out": sum(case.tokens_out for case in stats),
            }
        ),
        list_cost_usd_per_1k=(total_cost * 1000 / len(stats)).quantize(Decimal("0.000001"))
        if stats
        else None,
        n_errors=counts["error"],
        started_at=started_at,
        finished_at=finished_at,
    )


def git_sha(root: Path) -> str | None:  # noqa: PLR0911 - one early exit per git layout
    """HEAD commit read from ``.git`` files (no subprocess); ``None`` when unavailable."""
    try:
        git_dir = root / ".git"
        if git_dir.is_file():  # worktree / submodule: "gitdir: <path>"
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return None
            git_dir = (root / pointer.removeprefix("gitdir:").strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head if _is_sha(head) else None
        ref = head.removeprefix("ref:").strip()
        if not ref.startswith("refs/") or ".." in ref:
            return None
        ref_file = git_dir / ref
        if ref_file.is_file():
            sha = ref_file.read_text(encoding="utf-8").strip()
            return sha if _is_sha(sha) else None
        packed = git_dir / "packed-refs"
        if packed.is_file() and packed.stat().st_size <= _MAX_GIT_FILE_BYTES:
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and _is_sha(parts[0]):  # noqa: PLR2004
                    return parts[0]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _is_sha(text: str) -> bool:
    return len(text) == _GIT_SHA_LEN and all(c in "0123456789abcdef" for c in text)


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in _PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            continue
    return versions


def build_manifest(
    *,
    run_id: str,
    config: RunConfig,
    dataset: DatasetInfo,
    root: Path,
    started_at: datetime,
    kaggle: KaggleInfo | None = None,
) -> Manifest:
    return Manifest(
        run_id=run_id,
        resolved_config=config.model_dump(mode="json"),
        dataset=dataset,
        git_sha=git_sha(root),
        packages=package_versions(),
        python=sys.version.split()[0],
        platform=platform.platform(terse=True),
        started_at=started_at,
        kaggle=kaggle,
    )
