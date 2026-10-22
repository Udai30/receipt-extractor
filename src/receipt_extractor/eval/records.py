"""Shared run records (BACKEND_SCHEMA §S2): ``ResultRow``, ``summary.json``, ``manifest.json``.

These models are the file formats of ``runs/<run_id>/``; a change here is a format change and
lands together with BACKEND_SCHEMA.md. Money is ``Decimal`` (serialised as a string), times are
UTC ISO-8601.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

RESULTS_FILENAME: Final = "results.jsonl"
SUMMARY_FILENAME: Final = "summary.json"
MANIFEST_FILENAME: Final = "manifest.json"
LOCK_FILENAME: Final = ".lock"

CaseStatus = Literal["pass", "fail", "error"]

_RECORD = ConfigDict(extra="forbid")


class ResultRow(BaseModel):
    """One line of ``runs/<run_id>/results.jsonl``.

    ``details.metrics`` holds every configured metric's per-case value; ``details.error`` holds
    the sanitised exception text of an ``error`` row.
    """

    model_config = _RECORD

    case_id: str = Field(min_length=1, max_length=256)
    status: CaseStatus
    score: float | None
    prediction: Any = None
    gold: Any = None
    details: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = Field(ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    list_cost_usd: Decimal = Field(default=Decimal(0), ge=0)


class MetricValue(BaseModel):
    model_config = _RECORD

    value: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    n: int = Field(ge=0)


class LatencySummary(BaseModel):
    model_config = _RECORD

    p50: float | None
    p95: float | None
    p99: float | None = None


class TokenSummary(BaseModel):
    """Serialised as ``{"in": …, "out": …}`` (dump with ``by_alias=True``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    in_: int = Field(alias="in", ge=0)
    out: int = Field(ge=0)


class Summary(BaseModel):
    """``runs/<run_id>/summary.json`` — written only when every selected case has a row."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    run_id: str
    variant: str
    dataset_name: str
    dataset_split: str
    sample_size: int = Field(ge=0)
    primary_metric: str
    metrics: dict[str, MetricValue]
    status_counts: dict[CaseStatus, int]
    latency_ms: LatencySummary
    tokens: TokenSummary
    list_cost_usd_per_1k: Decimal | None
    n_errors: int = Field(ge=0)
    started_at: AwareDatetime
    finished_at: AwareDatetime


class DatasetInfo(BaseModel):
    model_config = _RECORD

    name: str
    revision: str | None
    split: str
    manifest_hash: str
    ids_hash: str
    n_cases: int = Field(ge=0)


class KaggleInfo(BaseModel):
    """Provenance of predictions computed in a Kaggle notebook (TRD §S5: GPU-seconds, $0)."""

    model_config = _RECORD

    notebook: str = Field(min_length=1, max_length=256)
    gpu: str = Field(min_length=1, max_length=64)
    gpu_seconds: float = Field(ge=0)


class Manifest(BaseModel):
    """``runs/<run_id>/manifest.json`` — written when the run starts, kept on resume."""

    model_config = _RECORD

    run_id: str
    resolved_config: dict[str, Any]
    dataset: DatasetInfo
    git_sha: str | None
    packages: dict[str, str]
    python: str
    platform: str
    started_at: datetime
    kaggle: KaggleInfo | None = None
