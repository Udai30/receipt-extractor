"""M2.4: importing Kaggle receipt replies — validated against ``Receipt``, ids must match."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.eval.errors import PredictionImportError
from receipt_extractor.eval.importer import import_predictions
from receipt_extractor.eval.records import SUMMARY_FILENAME, ResultRow, Summary

GOLD = {
    "items": [
        {"name": "KOPI SUSU", "qty": "2", "unit_price": None, "price": "36000"},
        {"name": "ROTI BAKAR", "qty": None, "unit_price": None, "price": "15000"},
    ],
    "subtotal": None,
    "tax": None,
    "total": "51000",
    "currency": "IDR",
}
GOOD_REPLY = (
    '```json\n{"items": [{"name": "KOPI SUSU", "qty": "2", "unit_price": null, "price": "36.000"},'
    ' {"name": "ROTI BAKAR", "qty": null, "unit_price": null, "price": "15.000"}],'
    ' "subtotal": null, "tax": null, "total": "51.000", "currency": "IDR"}\n```'
)
OFF_REPLY = '{"items": [{"name": "KOPI SUSU", "price": "36.000"}], "total": "51.000"}'
BAD_REPLY = '{"items": [{"name": "KOPI SUSU"}], "total": "lots"}'
CASES = ("r-0", "r-1", "r-2", "r-3")


@pytest.fixture
def project(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    cases = root / "data" / "cases.jsonl"
    cases.parent.mkdir(parents=True)
    cases.write_text(
        "".join(json.dumps({"case_id": c, "input": {}, "gold": GOLD}) + "\n" for c in CASES),
        encoding="utf-8",
    )
    return root


def write_output(project: Path, lines: list[dict[str, Any]], **manifest: Any) -> Path:
    out = project / "data" / "imports" / "qwen_vl"
    out.mkdir(parents=True, exist_ok=True)
    body = {
        "variant": "C",
        "dataset": {
            "kind": "jsonl",
            "name": "r",
            "split": "test",
            "path": str(project / "data" / "cases.jsonl"),
        },
        "models": ["qwen-vl-3b"],
        "metrics": ["field_f1", "valid_json", "reconcile"],
        "primary_metric": "field_f1",
        "kaggle": {"notebook": "qwen_vl_cord.ipynb", "gpu": "T4", "gpu_seconds": 812.5},
        "prediction_format": "receipt_reply",
        "prompt_version": "b-v1",
        **manifest,
    }
    (out / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    (out / "predictions.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    return out


def rows(run_dir: Path) -> dict[str, ResultRow]:
    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [ResultRow.model_validate_json(line) for line in lines]
    return {r.case_id: r for r in parsed}


async def test_replies_are_validated_and_become_extraction_results(project: Path) -> None:
    lines = [
        {
            "case_id": "r-0",
            "prediction": GOOD_REPLY,
            "latency_ms": 4100,
            "tokens_in": 1500,
            "tokens_out": 120,
        },
        {"case_id": "r-1", "prediction": OFF_REPLY, "latency_ms": 3900},
        {"case_id": "r-2", "prediction": BAD_REPLY, "latency_ms": 3800},
        {
            "case_id": "r-3",
            "prediction": '{"items": [',
            "latency_ms": 9000,
            "finish_reason": "length",
        },
    ]
    outcome = await import_predictions(write_output(project, lines), project)
    assert outcome.status == "completed"
    by_id = rows(outcome.run_dir)

    good = by_id["r-0"].prediction
    assert by_id["r-0"].status == "pass"
    assert good["pipeline"] == "C" and good["schema_valid"] is True and good["attempts"] == 1
    assert good["receipt"]["total"] == "51000"  # parse_amount read "51.000" (CORD convention)
    assert (good["model_alias"], good["prompt_version"]) == ("qwen-vl-3b", "b-v1")
    assert (good["reconciled"], good["needs_review"]) == (True, False)
    assert good["stage_latency_ms"]["llm"] == 4100 and good["list_cost_usd"] == "0"
    assert (good["tokens_in"], good["tokens_out"]) == (1500, 120)
    assert good["raw_text"] is None

    off = by_id["r-1"].prediction
    assert off["schema_valid"] is True
    assert (off["reconciled"], off["needs_review"]) == (False, True)

    bad = by_id["r-2"]
    assert bad.status == "fail" and bad.details["field_scores"]["valid_json"] is False
    assert bad.prediction["failure"] == "schema_invalid"
    assert bad.prediction["raw_text"] == BAD_REPLY and bad.prediction["needs_review"] is True

    assert by_id["r-3"].prediction["failure"] == "truncated"

    summary = Summary.model_validate_json((outcome.run_dir / SUMMARY_FILENAME).read_text())
    assert summary.metrics["valid_json"].value == 0.5
    assert summary.list_cost_usd_per_1k == 0


async def test_prediction_that_is_not_reply_text_is_rejected(project: Path) -> None:
    lines = [{"case_id": "r-0", "prediction": {"total": "1"}, "latency_ms": 1}]
    with pytest.raises(PredictionImportError, match="must be the reply text"):
        await import_predictions(write_output(project, lines), project)


@pytest.mark.parametrize(
    "lines",
    [
        [
            {"case_id": "r-0", "prediction": GOOD_REPLY, "latency_ms": 1},
            {"case_id": "test-0000", "prediction": GOOD_REPLY, "latency_ms": 1},
        ],
        [
            {"case_id": "r-0", "prediction": GOOD_REPLY, "latency_ms": 1},
            {"case_id": "r-0", "prediction": GOOD_REPLY, "latency_ms": 1},
        ],
    ],
    ids=["id-not-in-sample", "duplicate-id"],
)
async def test_mismatched_ids_reject_the_import_before_anything_is_written(
    project: Path, lines: list[dict[str, Any]]
) -> None:
    with pytest.raises(PredictionImportError, match=r"not in the manifest's sample|duplicate"):
        await import_predictions(write_output(project, lines), project)
    runs = project / "runs"
    assert not runs.exists() or not any(runs.rglob("results.jsonl"))


async def test_missing_cases_become_error_rows(project: Path) -> None:
    lines = [{"case_id": "r-0", "prediction": GOOD_REPLY, "latency_ms": 1}]
    outcome = await import_predictions(write_output(project, lines), project)
    by_id = rows(outcome.run_dir)
    assert by_id["r-0"].status == "pass"
    assert {by_id[c].status for c in ("r-1", "r-2", "r-3")} == {"error"}


async def test_default_format_keeps_predictions_as_given(project: Path) -> None:
    lines = [{"case_id": c, "prediction": GOLD, "latency_ms": 1} for c in CASES]
    outcome = await import_predictions(
        write_output(project, lines, prediction_format="prediction"), project
    )
    assert rows(outcome.run_dir)["r-0"].prediction == GOLD
