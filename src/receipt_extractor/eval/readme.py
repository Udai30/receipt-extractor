"""README tables generated from ``reports/`` (TRD §S5: no hand-typed numbers).

``update_readme`` rewrites the text between marker comments in the README::

    <!-- results:start --> ... <!-- results:end -->
    <!-- failures:start --> ... <!-- failures:end -->

The results table lists the test-split rows of ``reports/results.csv`` (the gold calibration row
is left out); the failure table counts ``reports/failures.csv`` rows by hand-written label and
variant. The model column comes from each run's manifest, so every cell traces to a run id.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path
from typing import Final

from receipt_extractor.eval.errors import RunCorruptError

_VARIANT_ORDER: Final = ("A-naive", "A", "B", "C", "C-local")
_LABEL_ORDER: Final = (
    "ocr_misread",
    "wrong_item_split",
    "hallucinated_item",
    "number_format",
    "other",
)
_MAX_BYTES: Final = 2_000_000


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _num(value: str, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except ValueError:
        return "—"


def _seconds(value: str) -> str:
    try:
        return f"{float(value) / 1000:.1f} s"
    except ValueError:
        return "—"


def run_model(runs_dir: Path, run_id: str) -> str:
    """The model alias a run used, from its manifest (``—`` when it has none)."""
    path = runs_dir / run_id / "manifest.json"
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        return "—"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        model = manifest["resolved_config"]["params"]["model"]
    except (ValueError, KeyError, TypeError):
        return "—"
    return str(model) if isinstance(model, str) else "—"


def results_table(results_csv: str, models: dict[str, str]) -> str:
    """Markdown table of the test-split rows, in a fixed variant order."""
    by_variant = {r["variant"]: r for r in _rows(results_csv) if r.get("subset") == "test"}
    lines = [
        "| Pipeline | Model | Field F1 [95% CI] | Valid JSON | Reconciled | p50 / p95 latency "
        "| List cost / 1k | Run |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for variant in _VARIANT_ORDER:
        row = by_variant.get(variant)
        if row is None:
            continue
        ci = f"{_num(row['value'])} [{_num(row['ci_low'])}, {_num(row['ci_high'])}]"
        latency = f"{_seconds(row['p50_ms'])} / {_seconds(row['p95_ms'])}"
        cost = f"${_num(row['list_cost_usd_per_1k'])}"
        run_id = row["run_id"]
        lines.append(
            f"| {variant} | {models.get(run_id, '—')} | {ci} | {_num(row['valid_json'], 2)} "
            f"| {_num(row['reconcile'], 2)} | {latency} | {cost} | `{run_id}` |"
        )
    return "\n".join(lines)


def failures_table(failures_csv: str) -> str:
    """Markdown table: failure label by variant, counted from the hand-labelled CSV."""
    rows = _rows(failures_csv)
    counts: Counter[tuple[str, str]] = Counter(
        (r["failure_label"] or "unlabelled", r["variant"]) for r in rows
    )
    variants = [v for v in _VARIANT_ORDER if any(r["variant"] == v for r in rows)]
    labels = [
        *(label for label in _LABEL_ORDER if any(counts[(label, v)] for v in variants)),
        *sorted({lab for lab, _ in counts} - set(_LABEL_ORDER)),
    ]
    lines = [
        "| Failure cause | " + " | ".join(variants) + " |",
        "|---|" + "---|" * len(variants),
    ]
    lines += [
        f"| {label} | " + " | ".join(str(counts[(label, v)]) for v in variants) + " |"
        for label in labels
    ]
    lines.append(
        "| **Total** | "
        + " | ".join(str(sum(counts[(lab, v)] for lab in labels)) for v in variants)
        + " |"
    )
    return "\n".join(lines)


def _replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    head, found, rest = text.partition(start)
    _, found_end, tail = rest.partition(end)
    if not found or not found_end:
        raise RunCorruptError(f"README.md has no '{start}' … '{end}' block")
    return f"{head}{start}\n{body}\n{end}{tail}"


def update_readme(
    readme: Path, results_csv: Path, failures_csv: Path | None, runs_dir: Path
) -> list[str]:
    """Rewrite the generated blocks in place; returns the names of the blocks written."""
    text = readme.read_text(encoding="utf-8")
    results = results_csv.read_text(encoding="utf-8")
    models = {r["run_id"]: run_model(runs_dir, r["run_id"]) for r in _rows(results)}
    written = ["results"]
    text = _replace_block(text, "results", results_table(results, models))
    if failures_csv is not None and failures_csv.is_file():
        text = _replace_block(text, "failures", failures_table(failures_csv.read_text("utf-8")))
        written.append("failures")
    readme.write_text(text, encoding="utf-8", newline="\n")
    return written
