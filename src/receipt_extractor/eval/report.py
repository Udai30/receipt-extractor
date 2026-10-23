"""Committed reports (BACKEND_SCHEMA §S3) rebuilt from finished runs (TRD §S4, APPFLOW §S3).

- ``build_reports`` → ``reports/results.csv``, ``reports/results.md`` and two charts
  (``primary_metric.png``: mean ± bootstrap CI per variant; ``cost_per_1k.png``: list $/1k).
  One row per (variant, dataset, split): when several completed runs share that key, the most
  recently finished one is used. Runs without ``summary.json`` (paused or interrupted) are not
  reported.
- ``export_failures`` → ``reports/failures.csv`` with every ``fail``/``error`` case of the
  reported runs. Hand-written ``failure_label`` / ``note`` / ``fixed_by`` values already in the
  file are kept on re-export, including rows of runs no longer reported.

Free-text cells are neutralised against spreadsheet formula injection. All files are written
atomically.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from receipt_extractor.eval.errors import RunCorruptError
from receipt_extractor.eval.io import atomic_write_text, iter_jsonl, read_json_object
from receipt_extractor.eval.records import (
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
    Manifest,
    ResultRow,
    Summary,
)

logger = logging.getLogger(__name__)

RESULTS_CSV: Final = "results.csv"
RESULTS_MD: Final = "results.md"
FAILURES_CSV: Final = "failures.csv"
METRIC_CHART: Final = "primary_metric.png"
COST_CHART: Final = "cost_per_1k.png"

RESULTS_COLUMNS: Final = (
    "variant",
    "dataset",
    "subset",
    "run_id",
    "sample_size",
    "primary_metric",
    "value",
    "ci_low",
    "ci_high",
    "p50_ms",
    "p95_ms",
    "wall_p50_ms",
    "wall_p95_ms",
    "list_cost_usd_per_1k",
    "n_errors",
    "notes",
)
FAILURE_COLUMNS: Final = (
    "run_id",
    "case_id",
    "variant",
    "status",
    "score",
    "failure_label",
    "note",
    "fixed_by",
)
_LABEL_COLUMNS: Final = ("failure_label", "note", "fixed_by")
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
_MAX_NOTE_CHARS: Final = 500

# Reference palette (dataviz skill): light chart surface, ink and categorical slot 1.
_SURFACE: Final = "#fcfcfb"
_INK: Final = "#0b0b0b"
_INK_SECONDARY: Final = "#52514e"
_MUTED: Final = "#898781"
_GRID: Final = "#e1e0d9"
_BASELINE: Final = "#c3c2b7"
_SERIES_1: Final = "#2a78d6"


@dataclass(frozen=True)
class ReportedRun:
    run_dir: Path
    summary: Summary
    manifest: Manifest

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.summary.variant, self.summary.dataset_name, self.summary.dataset_split)

    @property
    def label(self) -> str:
        return f"{self.summary.variant} · {self.summary.dataset_split}"


@dataclass(frozen=True)
class ReportResult:
    files: tuple[Path, ...]
    rows: int


def iter_completed_runs(runs_dir: Path) -> Iterator[ReportedRun]:
    """Every run directory with a valid ``summary.json`` and ``manifest.json``."""
    if not runs_dir.is_dir():
        return
    for run_dir in sorted(runs_dir.iterdir()):
        summary_path = run_dir / SUMMARY_FILENAME
        if not run_dir.is_dir() or not summary_path.is_file():
            continue
        try:
            summary = Summary.model_validate(read_json_object(summary_path))
            manifest = Manifest.model_validate(read_json_object(run_dir / MANIFEST_FILENAME))
        except (RunCorruptError, ValidationError):
            logger.warning("skipping run %s: unreadable summary or manifest", run_dir.name)
            continue
        if summary.run_id != run_dir.name or manifest.run_id != run_dir.name:
            logger.warning("skipping run %s: run id does not match its directory", run_dir.name)
            continue
        yield ReportedRun(run_dir=run_dir, summary=summary, manifest=manifest)


def select_latest(runs: Iterator[ReportedRun]) -> list[ReportedRun]:
    latest: dict[tuple[str, str, str], ReportedRun] = {}
    for run in runs:
        current = latest.get(run.key)
        if current is None or run.summary.finished_at > current.summary.finished_at:
            latest[run.key] = run
    return sorted(latest.values(), key=lambda r: (r.summary.dataset_split, r.summary.variant))


def safe_cell(value: str) -> str:
    """Prefix text a spreadsheet would evaluate as a formula."""
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _fmt(value: float | None, digits: int = 4) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def _notes(run: ReportedRun) -> str:
    kaggle = run.manifest.kaggle
    if kaggle is None:
        return ""
    return f"imported from Kaggle ({kaggle.gpu}, {kaggle.gpu_seconds:.0f} GPU-s, $0)"


def _secondary_metrics(runs: Sequence[ReportedRun]) -> list[str]:
    names = {
        name for run in runs for name in run.summary.metrics if name != run.summary.primary_metric
    }
    return sorted(names)


def _result_record(run: ReportedRun, secondary: Sequence[str]) -> dict[str, str]:
    summary = run.summary
    primary = summary.metrics.get(summary.primary_metric)
    cost = summary.list_cost_usd_per_1k
    service = summary.service_latency_ms or summary.latency_ms  # pre-M2.3 summaries: wall only
    record = {
        "variant": summary.variant,
        "dataset": summary.dataset_name,
        "subset": summary.dataset_split,
        "run_id": summary.run_id,
        "sample_size": str(summary.sample_size),
        "primary_metric": summary.primary_metric,
        "value": _fmt(primary.value if primary else None),
        "ci_low": _fmt(primary.ci_low if primary else None),
        "ci_high": _fmt(primary.ci_high if primary else None),
        # p50/p95 exclude free-tier pacing waits (TRD §S5); wall clock alongside.
        "p50_ms": _fmt(service.p50, 0),
        "p95_ms": _fmt(service.p95, 0),
        "wall_p50_ms": _fmt(summary.latency_ms.p50, 0),
        "wall_p95_ms": _fmt(summary.latency_ms.p95, 0),
        "list_cost_usd_per_1k": "" if cost is None else str(cost),
        "n_errors": str(summary.n_errors),
        "notes": safe_cell(_notes(run)),
    }
    for name in secondary:
        metric = summary.metrics.get(name)
        record[name] = _fmt(metric.value if metric else None)
    return record


def _csv_text(columns: Sequence[str], records: Sequence[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue()


def _md_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _markdown(runs: Sequence[ReportedRun], secondary: Sequence[str]) -> str:
    header = [
        *("Variant", "Subset", "n", "Primary metric", "Value [95% CI]"),
        *secondary,
        *("p50 ms", "p95 ms", "Wall p95 ms", "$/1k (list)", "Errors", "Run id", "Notes"),
    ]
    lines = [
        "# Results",
        "",
        "Generated by `uv run receipt_extractor report build` from the latest completed run of"
        " each variant. Do not edit by hand; numbers trace to the run id.",
        "",
        "Latency is per receipt, end to end, excluding time queued for free-tier rate limits;"
        " wall-clock p95 (including that queueing) is shown alongside.",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for run in runs:
        record = _result_record(run, secondary)
        value = record["value"]
        if record["ci_low"] and record["ci_high"]:
            value += f" [{record['ci_low']}, {record['ci_high']}]"
        cells = [
            record["variant"],
            record["subset"],
            record["sample_size"],
            record["primary_metric"],
            value,
            *(record[name] for name in secondary),
            record["p50_ms"],
            record["p95_ms"],
            record["wall_p95_ms"],
            record["list_cost_usd_per_1k"],
            record["n_errors"],
            f"`{record['run_id']}`",
            _notes(run),
        ]
        lines.append("| " + " | ".join(_md_cell(cell) for cell in cells) + " |")
    if not runs:
        lines.append("| " + " | ".join("—" for _ in header) + " |")
    return "\n".join(lines) + "\n"


def _write_charts(runs: Sequence[ReportedRun], out_dir: Path) -> list[Path]:
    """Horizontal single-series charts (one row per reported variant); matplotlib, Agg."""
    import matplotlib as mpl  # noqa: PLC0415 - heavy; only `report build` needs it

    mpl.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    labels = [run.label for run in runs]
    positions = list(range(len(runs)))[::-1]  # first row on top
    height = 1.2 + 0.45 * len(runs)
    written: list[Path] = []

    def frame(title: str, xlabel: str) -> tuple[Any, Any]:
        fig, ax = plt.subplots(figsize=(7.5, height), dpi=150)
        fig.patch.set_facecolor(_SURFACE)
        ax.set_facecolor(_SURFACE)
        ax.set_title(title, loc="left", color=_INK, fontsize=11)
        ax.set_xlabel(xlabel, color=_INK_SECONDARY, fontsize=9)
        ax.set_yticks(positions, labels, color=_INK_SECONDARY, fontsize=9)
        ax.tick_params(axis="x", colors=_MUTED, labelsize=8)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(_BASELINE)
        return fig, ax

    def save(fig: Any, name: str) -> None:
        path = out_dir / name
        fig.tight_layout()
        fig.savefig(path, facecolor=_SURFACE, metadata={"Software": None})
        plt.close(fig)
        written.append(path)

    # Primary metric: dot at the mean, whisker for the 95% bootstrap CI.
    metric_names = sorted({run.summary.primary_metric for run in runs})
    fig, ax = frame(
        f"Primary metric ({', '.join(metric_names)}) with 95% bootstrap CI", "mean per case"
    )
    for y, run in zip(positions, runs, strict=True):
        metric = run.summary.metrics.get(run.summary.primary_metric)
        if metric is None or metric.value is None:
            continue
        if metric.ci_low is not None and metric.ci_high is not None:
            ax.hlines(y, metric.ci_low, metric.ci_high, color=_SERIES_1, linewidth=2)
        ax.plot(metric.value, y, "o", color=_SERIES_1, markersize=8, markeredgecolor=_SURFACE)
        ax.annotate(
            f"{metric.value:.3f}",
            (metric.value, y),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            color=_INK,
            fontsize=8,
        )
    ax.set_xlim(0, 1.05)
    ax.set_ylim(-0.7, len(runs) - 0.3)
    save(fig, METRIC_CHART)

    # List cost per 1k cases (local / Kaggle models are $0 by definition).
    fig, ax = frame("List cost per 1,000 cases (USD)", "USD per 1k cases, dated list prices")
    costs = [float(run.summary.list_cost_usd_per_1k or Decimal(0)) for run in runs]
    ax.barh(positions, costs, height=0.5, color=_SERIES_1)
    for y, cost in zip(positions, costs, strict=True):
        ax.annotate(
            f"${cost:,.4g}",
            (cost, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            color=_INK,
            fontsize=8,
        )
    ax.set_xlim(0, max([*costs, 0.0]) * 1.2 or 1.0)
    ax.set_ylim(-0.7, len(runs) - 0.3)
    save(fig, COST_CHART)
    return written


def build_reports(runs_dir: Path, out_dir: Path, *, charts: bool = True) -> ReportResult:
    runs = select_latest(iter_completed_runs(runs_dir))
    secondary = _secondary_metrics(runs)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [_result_record(run, secondary) for run in runs]
    csv_path = out_dir / RESULTS_CSV
    md_path = out_dir / RESULTS_MD
    atomic_write_text(csv_path, _csv_text([*RESULTS_COLUMNS, *secondary], records))
    atomic_write_text(md_path, _markdown(runs, secondary))
    files = [csv_path, md_path]
    if charts and runs:
        files += _write_charts(runs, out_dir)
    return ReportResult(files=tuple(files), rows=len(runs))


def _read_existing_labels(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {
            (row.get("run_id") or "", row.get("case_id") or ""): {
                column: row.get(column) or "" for column in FAILURE_COLUMNS
            }
            for row in reader
        }


def export_failures(
    runs_dir: Path, out_dir: Path, *, run_ids: Sequence[str] = ()
) -> tuple[Path, int]:
    """Write ``failures.csv``; returns its path and the number of failures exported now."""
    runs = select_latest(iter_completed_runs(runs_dir))
    if run_ids:
        wanted = set(run_ids)
        runs = [run for run in iter_completed_runs(runs_dir) if run.summary.run_id in wanted]
    path = out_dir / FAILURES_CSV
    merged = _read_existing_labels(path)
    exported = 0
    for run in runs:
        results = run.run_dir / RESULTS_FILENAME
        for lineno, obj in iter_jsonl(results):
            try:
                row = ResultRow.model_validate(obj)
            except ValidationError:
                raise RunCorruptError(
                    f"{run.summary.run_id}/{RESULTS_FILENAME}:{lineno}: invalid row"
                ) from None
            if row.status == "pass":
                continue
            exported += 1
            key = (run.summary.run_id, row.case_id)
            previous = merged.get(key, {})
            error = str(row.details.get("error") or "")[:_MAX_NOTE_CHARS]
            merged[key] = {
                "run_id": run.summary.run_id,
                "case_id": row.case_id,
                "variant": run.summary.variant,
                "status": row.status,
                "score": _fmt(row.score),
                "failure_label": previous.get("failure_label", ""),
                "note": previous.get("note") or error,
                "fixed_by": previous.get("fixed_by", ""),
            }
    records = [
        {
            column: safe_cell(value) if column in _LABEL_COLUMNS else value
            for column, value in record.items()
        }
        for _, record in sorted(merged.items())
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, _csv_text(FAILURE_COLUMNS, records))
    return path, exported
