"""Fixtures for the evaluation framework tests: an isolated project root and config helpers.

Every test runs from an empty temp directory (``clean_env`` chdirs there), so ``data/``,
``runs/``, ``configs/`` and ``reports/`` are created fresh and path confinement is checked
against that root.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from receipt_extractor.eval.config import RunConfig
from receipt_extractor.eval.records import RESULTS_FILENAME, ResultRow
from receipt_extractor.eval.runner import PreparedRun, prepare_config


def base_config(size: int = 20, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "variant": "toy",
        "task": "toy",
        "params": {"error_rate": 0.2},
        "dataset": {"kind": "synthetic", "name": "toy", "split": "synthetic", "size": size},
        "metrics": ["exact_match"],
        "primary_metric": "exact_match",
        "seed": 0,
        "concurrency": 4,
    }
    config.update(overrides)
    return config


def read_rows(run_dir: Path) -> list[ResultRow]:
    lines = (run_dir / RESULTS_FILENAME).read_text(encoding="utf-8").splitlines()
    return [ResultRow.model_validate(json.loads(line)) for line in lines if line.strip()]


@pytest.fixture
def project(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The project root for this test (also the working directory)."""
    return tmp_path.resolve()


@pytest.fixture
def write_config(project: Path) -> Callable[..., Path]:
    """Write ``configs/runs/<name>.yaml`` and return its project-relative path."""

    def write(data: dict[str, Any] | str, name: str = "test") -> Path:
        path = project / "configs" / "runs" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
        path.write_text(text, encoding="utf-8")
        return path.relative_to(project)

    return write


@pytest.fixture
def prepare(project: Path) -> Callable[..., PreparedRun]:
    """Prepare a run directly from a config dict (no YAML file, no task registry)."""

    def build(size: int = 20, **overrides: Any) -> PreparedRun:
        return prepare_config(RunConfig.model_validate(base_config(size, **overrides)), project)

    return build
