"""``import-predictions``: turn a Kaggle notebook's output into a normal run directory.

Expected layout of the output directory (under ``data/``, e.g. ``data/imports/qwen_vl/``)::

    manifest.json       ImportManifest: variant, dataset + sample (as in a run config),
                        metrics, models, kaggle {notebook, gpu, gpu_seconds}
    predictions.jsonl   one PredictionLine per case: case_id, prediction | error, latency_ms,
                        optional tokens_in / tokens_out / details

The importer scores each prediction against the same dataset gold with the same metrics as a
local run, streaming line by line. Selected cases with no prediction become ``error`` rows
("no prediction"), so an incomplete notebook run is visible instead of silently scoring a
smaller sample. The run id hashes the manifest and the predictions file, so re-importing the
same files is a no-op and changed files produce a new run. Local models cost $0 (TRD §S5); the
GPU time is recorded in ``manifest.json``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import to_jsonable_python

from receipt_extractor.eval.config import BootstrapSpec, ModelAlias, RunConfig, SampleSpec
from receipt_extractor.eval.datasets import CaseId, DatasetSpec, Name, describe_validation_error
from receipt_extractor.eval.errors import PredictionImportError, RunConfigError
from receipt_extractor.eval.io import MAX_LINE_BYTES, read_json_object, sha256_file
from receipt_extractor.eval.paths import DATA_DIR, confine, relative_posix
from receipt_extractor.eval.records import KaggleInfo, ResultRow
from receipt_extractor.eval.runner import (
    RunOutcome,
    open_session,
    prepare_config,
    score_case,
)
from receipt_extractor.llm.errors import sanitize_error_text

IMPORT_MANIFEST_FILENAME: Final = "manifest.json"
PREDICTIONS_FILENAME: Final = "predictions.jsonl"
IMPORT_TASK: Final = "import"
MISSING_PREDICTION: Final = "MissingPrediction: no prediction in the imported output"
MAX_ERROR_CHARS: Final = 2000

logger = logging.getLogger(__name__)


class ImportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = 1
    variant: Name
    dataset: DatasetSpec
    sample: SampleSpec = Field(default_factory=SampleSpec)
    models: tuple[ModelAlias, ...] = ()
    metrics: tuple[Name, ...] = Field(min_length=1)
    primary_metric: Name
    pass_threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    seed: int = 0
    bootstrap: BootstrapSpec = Field(default_factory=BootstrapSpec)
    kaggle: KaggleInfo


class PredictionLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseId
    prediction: Any = None
    error: str | None = Field(default=None, max_length=MAX_ERROR_CHARS)
    latency_ms: int = Field(ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


def _load_manifest(path: Path, source: str) -> ImportManifest:
    if not path.is_file():
        raise PredictionImportError(f"{source} has no {IMPORT_MANIFEST_FILENAME}")
    try:
        return ImportManifest.model_validate(read_json_object(path))
    except ValidationError as exc:
        raise PredictionImportError(
            describe_validation_error(exc, f"{source}/{IMPORT_MANIFEST_FILENAME}")
        ) from None


def _run_config(manifest: ImportManifest, predictions_sha256: str) -> RunConfig:
    try:
        return RunConfig(
            variant=manifest.variant,
            task=IMPORT_TASK,
            params={
                "predictions_sha256": predictions_sha256,
                "kaggle": manifest.kaggle.model_dump(mode="json"),
            },
            dataset=manifest.dataset,
            sample=manifest.sample,
            models=manifest.models,
            metrics=manifest.metrics,
            primary_metric=manifest.primary_metric,
            pass_threshold=manifest.pass_threshold,
            seed=manifest.seed,
            bootstrap=manifest.bootstrap,
        )
    except ValidationError as exc:
        raise RunConfigError(describe_validation_error(exc, IMPORT_MANIFEST_FILENAME)) from None


def _iter_predictions(path: Path) -> Iterator[PredictionLine]:
    with path.open("rb") as handle:
        lineno = 0
        while line := handle.readline(MAX_LINE_BYTES + 1):
            lineno += 1
            if len(line) > MAX_LINE_BYTES:
                raise PredictionImportError(f"{PREDICTIONS_FILENAME}:{lineno}: line too long")
            if not line.strip():
                continue
            try:
                yield PredictionLine.model_validate(json.loads(line))
            except ValueError as exc:  # JSONDecodeError and ValidationError
                detail = (
                    describe_validation_error(exc, f"{PREDICTIONS_FILENAME}:{lineno}")
                    if isinstance(exc, ValidationError)
                    else f"{PREDICTIONS_FILENAME}:{lineno}: not valid JSON"
                )
                raise PredictionImportError(detail) from None


async def import_predictions(source_dir: str | Path, root: Path) -> RunOutcome:
    """Import a Kaggle output directory (confined to ``data/``) as a completed run."""
    source = confine(source_dir, root, [DATA_DIR])
    shown = relative_posix(source, root)
    if not source.is_dir():
        raise PredictionImportError(f"{shown} is not a directory")
    manifest = _load_manifest(source / IMPORT_MANIFEST_FILENAME, shown)
    predictions_path = confine(source / PREDICTIONS_FILENAME, root, [DATA_DIR])
    if not predictions_path.is_file():
        raise PredictionImportError(f"{shown} has no {PREDICTIONS_FILENAME}")

    prepared = prepare_config(_run_config(manifest, sha256_file(predictions_path)), root)
    config = prepared.config
    selected = set(prepared.case_ids)
    seen: set[str] = set()
    skipped = 0

    with open_session(prepared, kaggle=manifest.kaggle) as session:
        metrics = session.metrics
        for line in _iter_predictions(predictions_path):
            if line.case_id in seen:
                raise PredictionImportError(f"duplicate prediction for case {line.case_id!r}")
            seen.add(line.case_id)
            if line.case_id not in selected:
                skipped += 1
                continue
            if line.case_id in session.finished:
                continue
            gold = to_jsonable_python(prepared.dataset.load_case(line.case_id).gold)
            if line.error is not None:
                row = ResultRow(
                    case_id=line.case_id,
                    status="error",
                    score=None,
                    gold=gold,
                    details={"error": sanitize_error_text(line.error)},
                    latency_ms=line.latency_ms,
                    tokens_in=line.tokens_in,
                    tokens_out=line.tokens_out,
                )
            else:
                status, score, values = score_case(line.prediction, gold, config, metrics)
                row = ResultRow(
                    case_id=line.case_id,
                    status=status,
                    score=score,
                    prediction=line.prediction,
                    gold=gold,
                    details={**line.details, "metrics": values},
                    latency_ms=line.latency_ms,
                    tokens_in=line.tokens_in,
                    tokens_out=line.tokens_out,
                    list_cost_usd=Decimal(0),
                )
            await session.append(row)
        for case_id in session.pending():
            gold = to_jsonable_python(prepared.dataset.load_case(case_id).gold)
            await session.append(
                ResultRow(
                    case_id=case_id,
                    status="error",
                    score=None,
                    gold=gold,
                    details={"error": MISSING_PREDICTION},
                    latency_ms=0,
                )
            )
        outcome = session.outcome("completed", session.finalize())
    if skipped:
        logger.warning("ignored %d prediction(s) for case ids outside the sample", skipped)
    return outcome
