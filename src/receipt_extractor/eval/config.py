"""Run configs (``configs/runs/*.yaml``, TRD §S4) and the run id.

A config names the dataset and its sample, the task (variant implementation) and its params,
the model aliases, the metrics and the bootstrap settings. It is parsed with
``yaml.safe_load`` and validated strictly (unknown keys are errors).

**Run id** = ``<variant>-<12 hex>``: SHA-256 over the resolved config (dataset paths
normalised to project-relative POSIX form) plus the dataset manifest hash. ``concurrency`` and
``runs_dir`` are excluded — they change how a run executes, not what it measures — so a resume
with a different concurrency continues the same run.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Any, Final, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from receipt_extractor.eval.datasets import (
    CaseId,
    DatasetSpec,
    JSONLDatasetSpec,
    Name,
    describe_validation_error,
)
from receipt_extractor.eval.errors import RunConfigError
from receipt_extractor.eval.io import canonical_json
from receipt_extractor.eval.paths import CONFIGS_DIR, DATA_DIR, confine, relative_posix

MAX_CONFIG_BYTES: Final = 1024 * 1024
RUN_ID_FORMAT_VERSION: Final = 1
IDENTITY_EXCLUDE: Final = frozenset({"concurrency", "runs_dir"})
_RUN_ID_HEX_CHARS: Final = 12

ModelAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]


class SampleSpec(BaseModel):
    """Which cases to run: explicit ``ids``, a seeded random ``size``, or (neither) all cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ids: tuple[CaseId, ...] | None = Field(default=None, min_length=1, max_length=1_000_000)
    size: int | None = Field(default=None, ge=1)
    seed: int = 0

    @model_validator(mode="after")
    def _one_selector(self) -> Self:
        if self.ids is not None and self.size is not None:
            raise ValueError("give either sample.ids or sample.size, not both")
        if self.ids is not None and len(set(self.ids)) != len(self.ids):
            raise ValueError("sample.ids contains duplicates")
        return self


class BootstrapSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resamples: int = Field(default=1000, ge=100, le=100_000)
    confidence: float = Field(default=0.95, gt=0.5, lt=1.0)


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    variant: Name
    task: Name
    params: dict[str, Any] = Field(default_factory=dict)
    dataset: DatasetSpec
    sample: SampleSpec = Field(default_factory=SampleSpec)
    models: tuple[ModelAlias, ...] = ()
    metrics: tuple[Name, ...] = Field(min_length=1)
    primary_metric: Name
    pass_threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    seed: int = 0
    bootstrap: BootstrapSpec = Field(default_factory=BootstrapSpec)
    concurrency: int = Field(default=4, ge=1, le=64)
    case_timeout_s: float = Field(default=600.0, gt=0, le=3600)
    runs_dir: str = Field(default="runs", min_length=1, max_length=1024)

    @model_validator(mode="after")
    def _consistent_metrics(self) -> Self:
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("metrics contains duplicates")
        if self.primary_metric not in self.metrics:
            raise ValueError("primary_metric must be one of metrics")
        return self

    def identity(self) -> dict[str, Any]:
        """The fields that define what a run measures (hashed into the run id)."""
        return self.model_dump(mode="json", exclude=set(IDENTITY_EXCLUDE))


def normalise_paths(config: RunConfig, root: Path) -> RunConfig:
    """Confine config paths and rewrite them as project-relative POSIX strings."""
    dataset = config.dataset
    if isinstance(dataset, JSONLDatasetSpec):
        path = confine(dataset.path, root, [DATA_DIR])
        dataset = dataset.model_copy(update={"path": relative_posix(path, root)})
    return config.model_copy(update={"dataset": dataset})


def compute_run_id(config: RunConfig, dataset_manifest_hash: str) -> str:
    payload = {
        "format": RUN_ID_FORMAT_VERSION,
        "config": config.identity(),
        "dataset_manifest_hash": dataset_manifest_hash,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{config.variant}-{digest[:_RUN_ID_HEX_CHARS]}"


def parse_run_config(data: object, source: str) -> RunConfig:
    if not isinstance(data, dict):
        raise RunConfigError(f"{source} must be a mapping of config keys")
    try:
        return RunConfig.model_validate(data)
    except ValidationError as exc:
        raise RunConfigError(describe_validation_error(exc, source)) from None


def load_run_config(path: str | Path, root: Path) -> RunConfig:
    """Read a run config from ``configs/`` with ``yaml.safe_load`` and validate it."""
    resolved = confine(path, root, [CONFIGS_DIR])
    source = relative_posix(resolved, root)
    try:
        if resolved.stat().st_size > MAX_CONFIG_BYTES:
            raise RunConfigError(f"{source} is larger than {MAX_CONFIG_BYTES} bytes")
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RunConfigError(f"{source} does not exist") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise RunConfigError(f"{source} cannot be read ({type(exc).__name__})") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise RunConfigError(f"{source} is not valid YAML") from None
    return parse_run_config(data, source)
