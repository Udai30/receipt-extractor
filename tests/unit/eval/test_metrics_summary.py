"""Metric registry, bootstrap CI determinism and summary statistics."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from receipt_extractor.eval.config import RunConfig
from receipt_extractor.eval.errors import UnknownMetricError
from receipt_extractor.eval.metrics import (
    ExactMatch,
    bootstrap_ci,
    get_metric,
    quantile,
    register_metric,
)
from receipt_extractor.eval.summary import build_summary, git_sha

from .conftest import base_config

VALUES = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0] * 3


def test_bootstrap_ci_is_deterministic_with_fixed_seed() -> None:
    first = bootstrap_ci(VALUES, resamples=1000, confidence=0.95, seed="0:exact_match")
    second = bootstrap_ci(VALUES, resamples=1000, confidence=0.95, seed="0:exact_match")
    other = bootstrap_ci(VALUES, resamples=1000, confidence=0.95, seed="1:exact_match")
    assert first == second
    assert first != other
    assert first is not None
    low, high = first
    assert low < sum(VALUES) / len(VALUES) < high
    assert 0.0 <= low <= high <= 1.0


def test_bootstrap_edge_cases() -> None:
    assert bootstrap_ci([], resamples=100, confidence=0.95, seed=0) is None
    assert bootstrap_ci([0.5], resamples=100, confidence=0.95, seed=0) == (0.5, 0.5)


def test_quantile_matches_linear_interpolation() -> None:
    assert quantile([], 0.5) is None
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert quantile([10.0], 0.95) == 10.0
    assert quantile([0.0, 10.0], 0.95) == pytest.approx(9.5)


def test_exact_match_and_registry() -> None:
    metric = get_metric("exact_match")
    assert metric.compute({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1}) == 1.0
    assert metric.compute({"a": 1}, {"a": 2}) == 0.0
    with pytest.raises(UnknownMetricError):
        get_metric("nope")
    with pytest.raises(ValueError, match="already registered"):
        register_metric(ExactMatch())


def _row(case_id: str, status: str, score: float | None, latency: int) -> str:
    metrics = {} if status == "error" else {"exact_match": score}
    return json.dumps(
        {
            "case_id": case_id,
            "status": status,
            "score": score,
            "details": {"metrics": metrics},
            "latency_ms": latency,
            "tokens_in": 10,
            "tokens_out": 2,
            "list_cost_usd": "0.001",
        }
    )


def test_summary_counts_errors_as_zero_and_dedupes(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    lines = [
        _row("a", "pass", 1.0, 100),
        _row("b", "fail", 0.0, 200),
        _row("c", "error", None, 300),
        _row("d", "pass", 1.0, 400),
        _row("d", "pass", 1.0, 400),  # duplicate line: last one wins, counted once
    ]
    results.write_text("\n".join(lines) + "\n", encoding="utf-8")
    now = datetime.now(UTC)
    summary = build_summary(
        results,
        run_id="toy-x",
        config=RunConfig.model_validate(base_config()),
        dataset_name="toy",
        started_at=now,
        finished_at=now,
    )
    assert summary.sample_size == 4
    assert summary.metrics["exact_match"].value == 0.5  # (1 + 0 + 0 + 1) / 4
    assert summary.metrics["exact_match"].n == 4
    assert summary.status_counts == {"pass": 2, "fail": 1, "error": 1}
    assert summary.latency_ms.p50 == 250.0
    assert summary.latency_ms.p99 is None  # only reported from 500 cases
    assert summary.tokens.in_ == 40
    assert str(summary.list_cost_usd_per_1k) == "1.000000"
    dumped = json.loads(summary.model_dump_json(by_alias=True))
    assert dumped["tokens"] == {"in": 40, "out": 8}


def test_git_sha_reads_head_without_git(tmp_path: Path) -> None:
    sha = "a" * 40
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/test\n")
    (git / "refs" / "heads" / "test").write_text(sha + "\n")
    assert git_sha(tmp_path) == sha
    (git / "refs" / "heads" / "test").unlink()
    (git / "packed-refs").write_text(f"# pack-refs\n{sha} refs/heads/test\n")
    assert git_sha(tmp_path) == sha
    (git / "HEAD").write_text("ref: refs/../../../etc/passwd\n")
    assert git_sha(tmp_path) is None
    assert git_sha(tmp_path / "missing") is None
