"""Results and Failures tabs: read the committed ``reports/`` files (UI_UX_DESIGN §2.1, §2.3).

Nothing here computes a number: the table, headline and charts come from
``reports/results.csv`` and the PNGs written by ``cli report build``, so the Space can never show a
figure that no run produced (CLAUDE.md §5). Missing or partial reports are an explicit state, not
an error (the benchmark runs over several free-tier days).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from receipt_extractor.eval.report import COST_CHART, FAILURES_CSV, METRIC_CHART, RESULTS_CSV

__all__ = [
    "FAILURES_CSV",
    "FAILURE_EXAMPLES",
    "RESULT_COLUMNS",
    "FailureView",
    "ResultsView",
    "failure_detail",
    "filter_failures",
    "load_failures",
    "load_results",
]

FAILURE_EXAMPLES: Final = "failure_examples.jsonl"  # gold vs predicted per failure (M3.1)
MAX_REPORT_BYTES: Final = 5 * 1024 * 1024
ALL: Final = "All"
RESULT_COLUMNS: Final = (
    "Pipeline",
    "Field F1 [95% CI]",
    "Valid JSON %",
    "Reconcile %",
    "p50 s",
    "p95 s",
    "$ / 1k receipts",
    "n",
)
FAILURE_COLUMNS: Final = ("Pipeline", "Case", "Cause", "Score", "Note")
_LABELS: Final = {
    "A-naive": "A-naive · OCR + LLM, no checks",
    "A": "A · OCR + LLM (engineered)",
    "B": "B · Gemini vision",
    "C": "C · Qwen2.5-VL-3B (Kaggle)",
    "C-local": "C-local · Qwen3-VL-4B (CPU)",
    "gold": "gold · scorer calibration",
}
_ORDER: Final = ("A-naive", "A", "B", "C", "C-local", "gold")


@dataclass(frozen=True, slots=True)
class ResultsView:
    headline: str
    columns: tuple[str, ...]
    rows: list[list[str]]
    charts: list[tuple[Path, str]] = field(default_factory=list)  # (png, alt text)
    note: str = ""


@dataclass(frozen=True, slots=True)
class FailureView:
    columns: tuple[str, ...]
    rows: list[list[str]]
    causes: list[str]
    pipelines: list[str]
    note: str = ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _num(text: str | None) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _pct(text: str | None) -> str:
    value = _num(text)
    return "—" if value is None else f"{value * 100:.0f} %"


def _seconds(text: str | None) -> str:
    value = _num(text)
    return "—" if value is None else f"{value / 1000:.1f}"


def _f1(row: dict[str, str]) -> str:
    value, low, high = _num(row.get("value")), _num(row.get("ci_low")), _num(row.get("ci_high"))
    if value is None:
        return "—"
    if low is None or high is None:
        return f"{value:.3f}"
    return f"{value:.3f} [{low:.3f}, {high:.3f}]"


def _cost(text: str | None) -> str:
    value = _num(text)
    return "—" if value is None else f"${value:.4f}"


def _headline(by_variant: dict[str, dict[str, str]]) -> str:
    a, b = by_variant.get("A"), by_variant.get("B")
    if a is None or b is None:
        return (
            "Benchmark in progress: the full 100-receipt runs are spread over several free-tier "
            "days. The table shows every completed run so far."
        )
    fa, fb = _num(a.get("value")), _num(b.get("value"))
    ca, cb = _num(a.get("list_cost_usd_per_1k")), _num(b.get("list_cost_usd_per_1k"))
    if fa is None or fb is None:
        return "Results table below."
    ratio = ""
    if ca and cb and ca > 0:
        ratio = f" at {cb / ca:.1f}x the cost"
    return f"Gemini vision: {fb:.2f} field F1 vs OCR + LLM {fa:.2f}{ratio} (CORD-v2 test)."


def load_results(reports_dir: Path) -> ResultsView:
    """The Results tab from ``reports/results.csv`` (test rows only) and the report charts.

    ``results.csv`` also holds the small validation runs of the same variants; keying by variant
    without the subset filter let those 10-receipt rows overwrite the 100-receipt test rows.
    """
    rows = _read_csv(reports_dir / RESULTS_CSV)
    by_variant = {
        row["variant"]: row for row in rows if row.get("variant") and row.get("subset") == "test"
    }
    ordered = sorted(
        by_variant.values(),
        key=lambda r: (_ORDER.index(r["variant"]) if r["variant"] in _ORDER else 99, r["variant"]),
    )
    table = [
        [
            _LABELS.get(row["variant"], row["variant"]),
            _f1(row),
            _pct(row.get("valid_json")),
            _pct(row.get("reconcile")),
            _seconds(row.get("p50_ms")),
            _seconds(row.get("p95_ms")),
            _cost(row.get("list_cost_usd_per_1k")),
            row.get("sample_size", ""),
        ]
        for row in ordered
    ]
    charts = [
        (reports_dir / name, alt)
        for name, alt in (
            (METRIC_CHART, "Bar chart of field F1 per pipeline (same values as the table)"),
            (COST_CHART, "Bar chart of list cost per 1,000 receipts (same values as the table)"),
        )
        if (reports_dir / name).is_file()
    ]
    note = (
        "Latency excludes time queued for free-tier rate limits. "
        "List cost uses dated paid-tier prices; actual spend is $0 (free tiers)."
    )
    if not table:
        note = "No results yet: run `receipt_extractor report build` after the first run."
    return ResultsView(_headline(by_variant), RESULT_COLUMNS, table, charts, note)


def load_failures(reports_dir: Path) -> FailureView:
    rows = _read_csv(reports_dir / FAILURES_CSV)
    table = [
        [
            r.get("variant", ""),
            r.get("case_id", ""),
            r.get("failure_label") or "unlabelled",
            r.get("score", ""),
            r.get("note", "")[:300],
        ]
        for r in rows
    ]
    note = (
        ""
        if table
        else ("No failure list yet: it is exported and labelled after the full test runs (M3.1).")
    )
    return FailureView(
        columns=FAILURE_COLUMNS,
        rows=table,
        causes=[ALL, *sorted({row[2] for row in table})],
        pipelines=[ALL, *sorted({row[0] for row in table})],
        note=note,
    )


def filter_failures(view: FailureView, cause: str, pipeline: str) -> list[list[str]]:
    return [
        row
        for row in view.rows
        if (cause in (ALL, "") or row[2] == cause) and (pipeline in (ALL, "") or row[0] == pipeline)
    ]


def failure_detail(
    reports_dir: Path, rows: Sequence[Sequence[str]], index: int | None
) -> tuple[str, str]:
    """Gold vs predicted JSON for a selected row, from ``reports/failure_examples.jsonl``."""
    if index is None or not 0 <= index < len(rows):
        return "", ""
    pipeline, case_id = rows[index][0], rows[index][1]
    path = reports_dir / FAILURE_EXAMPLES
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        return "", "Gold vs predicted is exported with the labelled failures (M3.1)."
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            example: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        if example.get("variant") == pipeline and example.get("case_id") == case_id:
            gold = json.dumps(example.get("gold"), indent=2, ensure_ascii=False)
            predicted = json.dumps(example.get("prediction"), indent=2, ensure_ascii=False)
            return gold, predicted
    return "", "No stored example for this row."
