"""Kaggle prediction import, results/failures reports and charts."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.eval.datasets import SyntheticDataset, SyntheticDatasetSpec
from receipt_extractor.eval.errors import PathConfinementError, PredictionImportError
from receipt_extractor.eval.importer import MISSING_PREDICTION, import_predictions
from receipt_extractor.eval.records import (
    MANIFEST_FILENAME,
    SUMMARY_FILENAME,
    Manifest,
    Summary,
)
from receipt_extractor.eval.report import (
    COST_CHART,
    FAILURES_CSV,
    METRIC_CHART,
    RESULTS_CSV,
    RESULTS_MD,
    build_reports,
    export_failures,
    safe_cell,
)
from receipt_extractor.eval.runner import PreparedRun, execute_run
from receipt_extractor.eval.tasks import build_task

from .conftest import read_rows

Prepare = Callable[..., PreparedRun]
DATASET = {"kind": "synthetic", "name": "toy", "split": "synthetic", "size": 20, "seed": 0}


def write_kaggle_output(project: Path, lines: list[dict[str, Any]], name: str = "qwen_vl") -> Path:
    out = project / "data" / "imports" / name
    out.mkdir(parents=True)
    manifest = {
        "variant": "C",
        "dataset": DATASET,
        "metrics": ["exact_match"],
        "primary_metric": "exact_match",
        "models": ["qwen-vl-3b"],
        "kaggle": {"notebook": "notebooks/qwen_vl_cord.ipynb", "gpu": "T4", "gpu_seconds": 812.5},
    }
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (out / "predictions.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    return out.relative_to(project)


def fake_predictions() -> list[dict[str, Any]]:
    dataset = SyntheticDataset(SyntheticDatasetSpec.model_validate(DATASET))
    lines: list[dict[str, Any]] = []
    for i, case_id in enumerate(dataset.case_ids()):
        if i == 19:
            continue  # notebook never produced this one
        gold = dataset.load_case(case_id).gold
        line: dict[str, Any] = {"case_id": case_id, "latency_ms": 900 + i}
        if i == 5:
            line["error"] = "CUDA out of memory"
        else:
            line["prediction"] = {"sum": gold["sum"] + (1 if i % 4 == 0 else 0)}
        lines.append(line)
    lines.append({"case_id": "not-in-sample", "prediction": {}, "latency_ms": 1})
    return lines


async def test_import_of_fake_kaggle_output_produces_valid_run_dir(project: Path) -> None:
    source = write_kaggle_output(project, fake_predictions())
    outcome = await import_predictions(source, project)

    assert outcome.status == "completed"
    run_dir = outcome.run_dir
    assert run_dir.parent == project / "runs"
    rows = {r.case_id: r for r in read_rows(run_dir)}
    assert len(rows) == 20
    assert rows["toy-00005"].status == "error"
    assert rows["toy-00005"].details["error"] == "CUDA out of memory"
    assert rows["toy-00019"].details["error"] == MISSING_PREDICTION
    assert rows["toy-00001"].status == "pass"
    assert rows["toy-00004"].status == "fail"
    assert all(r.list_cost_usd == 0 for r in rows.values())

    manifest = Manifest.model_validate_json((run_dir / MANIFEST_FILENAME).read_text())
    summary = Summary.model_validate_json((run_dir / SUMMARY_FILENAME).read_text())
    assert manifest.kaggle is not None
    assert manifest.kaggle.gpu_seconds == 812.5
    assert manifest.resolved_config["task"] == "import"
    assert summary.variant == "C"
    assert summary.sample_size == 20
    assert summary.n_errors == 2
    # 18 predictions, 5 deliberately wrong (i % 4 == 0 among non-error rows: 0, 4, 8, 12, 16)
    assert summary.metrics["exact_match"].value == pytest.approx(13 / 20)

    # Re-importing the same files is a no-op on the same run id.
    again = await import_predictions(source, project)
    assert again.run_id == outcome.run_id
    assert again.executed == 0
    assert len(read_rows(run_dir)) == 20


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([{"case_id": "toy-00000", "latency_ms": 1}] * 2, "duplicate"),
        ([{"case_id": "toy-00000", "latency_ms": -1}], "latency_ms"),
        ([{"case_id": "toy-00000", "latency_ms": 1, "extra": True}], "extra"),
    ],
)
async def test_malformed_kaggle_output_is_rejected(
    project: Path, lines: list[dict[str, Any]], message: str
) -> None:
    source = write_kaggle_output(project, lines)
    with pytest.raises(PredictionImportError, match=message):
        await import_predictions(source, project)


async def test_import_directory_is_confined_to_data(project: Path) -> None:
    with pytest.raises(PathConfinementError):
        await import_predictions("../elsewhere", project)
    with pytest.raises(PathConfinementError):
        await import_predictions("configs", project)
    (project / "data").mkdir()
    with pytest.raises(PredictionImportError, match=r"manifest\.json"):
        await import_predictions("data", project)


async def test_reports_include_runs_and_imports(project: Path, prepare: Prepare) -> None:
    toy = prepare(20)
    await execute_run(toy, build_task("toy", {"error_rate": 0.2, "raise_ids": ["toy-00002"]}))
    await import_predictions(write_kaggle_output(project, fake_predictions()), project)
    # A paused/unfinished run (no summary.json) must not be reported.
    unfinished = prepare(5, seed=7)
    unfinished.run_dir.mkdir(parents=True)

    reports = project / "reports"
    result = build_reports(project / "runs", reports)
    assert result.rows == 2
    assert {p.name for p in result.files} == {RESULTS_CSV, RESULTS_MD, METRIC_CHART, COST_CHART}
    assert (reports / METRIC_CHART).read_bytes().startswith(b"\x89PNG")

    with (reports / RESULTS_CSV).open(encoding="utf-8", newline="") as handle:
        rows = {row["variant"]: row for row in csv.DictReader(handle)}
    assert set(rows) == {"toy", "C"}
    assert rows["toy"]["run_id"] == toy.run_id
    assert rows["toy"]["sample_size"] == "20"
    assert rows["toy"]["n_errors"] == "1"
    assert (
        float(rows["toy"]["ci_low"]) <= float(rows["toy"]["value"]) <= float(rows["toy"]["ci_high"])
    )
    assert "T4" in rows["C"]["notes"]
    assert rows["C"]["list_cost_usd_per_1k"] == "0.000000"
    markdown = (reports / RESULTS_MD).read_text(encoding="utf-8")
    assert toy.run_id in markdown
    assert "Do not edit by hand" in markdown


async def test_report_uses_latest_run_per_variant(project: Path, prepare: Prepare) -> None:
    older = prepare(5, seed=1)
    await execute_run(older, build_task("toy", {}))
    newer = prepare(5, seed=2)
    await execute_run(newer, build_task("toy", {}))
    build_reports(project / "runs", project / "reports", charts=False)
    text = (project / "reports" / RESULTS_CSV).read_text(encoding="utf-8")
    assert newer.run_id in text
    assert older.run_id not in text


async def test_failures_export_keeps_hand_labels(project: Path, prepare: Prepare) -> None:
    run = prepare(20)
    await execute_run(run, build_task("toy", {"error_rate": 0.3, "raise_ids": ["toy-00002"]}))
    reports = project / "reports"
    path, count = export_failures(project / "runs", reports)
    assert path == reports / FAILURES_CSV
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert count == len(rows) > 1
    assert all(row["status"] in {"fail", "error"} for row in rows)
    error_row = next(row for row in rows if row["case_id"] == "toy-00002")
    assert error_row["note"].startswith("ValueError")

    # A human labels one row; a malicious-looking label is neutralised; re-export keeps both.
    rows[0]["failure_label"] = "number format"
    rows[0]["note"] = '=HYPERLINK("http://x")'
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    export_failures(project / "runs", reports)
    with path.open(encoding="utf-8", newline="") as handle:
        again = {row["case_id"]: row for row in csv.DictReader(handle)}
    assert again[rows[0]["case_id"]]["failure_label"] == "number format"
    assert again[rows[0]["case_id"]]["note"] == '\'=HYPERLINK("http://x")'
    assert len(again) == len(rows)


def test_safe_cell() -> None:
    assert safe_cell("=1+1") == "'=1+1"
    assert safe_cell("@cmd") == "'@cmd"
    assert safe_cell("plain") == "plain"


def test_report_on_empty_runs_dir(project: Path) -> None:
    result = build_reports(project / "runs", project / "reports")
    assert result.rows == 0
    assert (project / "reports" / RESULTS_CSV).read_text().startswith("variant,")
