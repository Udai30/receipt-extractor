"""Demo view-models: column states, money formatting, Results/Failures from report files."""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.core.extraction import ExtractionResult, StageLatency
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.ui.reports import (
    ALL,
    FAILURE_EXAMPLES,
    failure_detail,
    filter_failures,
    load_failures,
    load_results,
)
from receipt_extractor.ui.views import column_view, format_money

RECEIPT = Receipt.model_validate(
    {
        "items": [
            {"name": "KOPI SUSU", "qty": "2", "price": "36.000"},
            {"name": "ROTI BAKAR", "price": "15.000"},
        ],
        "total": "51.000",
    }
)


def result(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pipeline": "A",
        "receipt": RECEIPT,
        "schema_valid": True,
        "attempts": 1,
        "reconciled": True,
        "needs_review": False,
        "reconcile_diff": Decimal(0),
        "model_alias": "groq-oss-20b",
        "stage_latency_ms": StageLatency(ocr=1600, llm=2100, validate_=1),
        "tokens_in": 1000,
        "tokens_out": 200,
        "list_cost_usd": Decimal("0.0001350"),
    }
    base.update(overrides)
    return ExtractionResult(**base).model_dump(mode="json")


# --- views ---------------------------------------------------------------------------------


def test_reconciled_column() -> None:
    view = column_view("A", result(), ocr_text="KOPI SUSU | 36.000")
    assert view.badge.level == "ok" and "Totals add up" in view.badge.text
    assert view.items == [
        ["KOPI SUSU", "2", "Rp 36.000 (36000)"],
        ["ROTI BAKAR", "", "Rp 15.000 (15000)"],
    ]
    assert ["Total", "Rp 51.000 (51000)"] in view.totals and ["Tax", "—"] in view.totals
    assert "groq-oss-20b" in view.metadata and "OCR 1.6 s" in view.metadata
    assert "1,000 in / 200 out tokens" in view.metadata and "1 call" in view.metadata
    assert '"total": "51000"' in view.raw_json and view.ocr_text == "KOPI SUSU | 36.000"


def test_not_reconciled_column_shows_the_difference_in_text() -> None:
    view = column_view(
        "A", result(reconciled=False, needs_review=True, reconcile_diff=Decimal(-9000))
    )
    assert view.badge.level == "warn"
    assert "needs review" in view.badge.text and "Rp -9.000" in view.badge.text


def test_invalid_column_shows_the_raw_reply() -> None:
    view = column_view(
        "B",
        result(
            receipt=None,
            schema_valid=False,
            failure="schema_invalid",
            attempts=3,
            reconciled=None,
            reconcile_diff=None,
            needs_review=True,
            raw_text="{oops",
        ),
    )
    assert view.badge.level == "error" and "invalid JSON" in view.badge.text
    assert view.items == [] and view.raw_json == "{oops" and view.attempts == 3
    assert "3 calls" in view.metadata


@pytest.mark.parametrize(
    ("overrides", "level", "text"),
    [
        (
            {"reconciled": None, "needs_review": None, "reconcile_diff": None},
            "neutral",
            "not checked",
        ),
        (
            {
                "receipt": None,
                "schema_valid": False,
                "failure": "ocr_empty",
                "attempts": 0,
                "reconciled": None,
                "reconcile_diff": None,
            },
            "error",
            "no text found",
        ),
        (
            {
                "receipt": None,
                "schema_valid": False,
                "failure": "truncated",
                "reconciled": None,
                "reconcile_diff": None,
            },
            "error",
            "truncated",
        ),
    ],
)
def test_other_badge_states(overrides: dict[str, Any], level: str, text: str) -> None:
    badge = column_view("A", result(**overrides)).badge
    assert badge.level == level and text in badge.text


def test_column_view_rejects_a_malformed_prediction() -> None:
    with pytest.raises(ValueError, match="validation error"):
        column_view("A", {"pipeline": "A"})


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        (Decimal(1380000), "IDR", "Rp 1.380.000 (1380000)"),
        ("51000", "IDR", "Rp 51.000 (51000)"),
        (Decimal("12.50"), "USD", "USD 12.50 (12.50)"),
        (None, "IDR", "—"),
        ("not a number", "IDR", "—"),
    ],
)
def test_format_money(value: Any, currency: str, expected: str) -> None:
    assert format_money(value, currency) == expected


# --- Results / Failures ----------------------------------------------------------------------


COLUMNS = [
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
    "valid_json",
    "reconcile",
]


def write_results(reports: Path, rows: list[dict[str, str]]) -> None:
    reports.mkdir(parents=True, exist_ok=True)
    with (reports / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})


def row(variant: str, value: str, cost: str) -> dict[str, str]:
    return {
        "variant": variant,
        "subset": "test",
        "run_id": f"{variant}-x",
        "sample_size": "100",
        "value": value,
        "ci_low": "0.8",
        "ci_high": "0.95",
        "p50_ms": "2100",
        "p95_ms": "6400",
        "list_cost_usd_per_1k": cost,
        "valid_json": "0.97",
        "reconcile": "0.81",
    }


def test_results_headline_and_table_come_from_the_csv(tmp_path: Path) -> None:
    write_results(
        tmp_path,
        [row("B", "0.91", "0.900000"), row("A", "0.88", "0.300000"), row("gold", "1.0", "0")],
    )
    view = load_results(tmp_path)

    assert view.headline == (
        "Gemini vision: 0.91 field F1 vs OCR + LLM 0.88 at 3.0x the cost (CORD-v2 test)."
    )
    assert [r[0].split(" ")[0] for r in view.rows] == ["A", "B", "gold"]  # fixed order
    first = view.rows[0]
    assert first[1] == "0.880 [0.800, 0.950]" and first[2] == "97 %" and first[3] == "81 %"
    assert (first[4], first[5], first[6], first[7]) == ("2.1", "6.4", "$0.3000", "100")
    assert "free-tier rate limits" in view.note and view.charts == []


def test_results_in_progress_and_empty_states(tmp_path: Path) -> None:
    write_results(tmp_path, [row("A", "0.88", "0.3")])
    assert load_results(tmp_path).headline.startswith("Benchmark in progress")
    empty = load_results(tmp_path / "nothing")
    assert empty.rows == [] and "No results yet" in empty.note


def test_results_charts_are_listed_with_alt_text(tmp_path: Path) -> None:
    write_results(tmp_path, [row("A", "0.88", "0.3")])
    (tmp_path / "primary_metric.png").write_bytes(b"\x89PNG")
    ((path, alt),) = load_results(tmp_path).charts
    assert path.name == "primary_metric.png" and "same values as the table" in alt


def test_failures_filter_and_detail(tmp_path: Path) -> None:
    with (tmp_path / "failures.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["run_id", "case_id", "variant", "status", "score", "failure_label", "note", "fixed_by"]
        )
        writer.writerow(["r1", "test-0001", "A", "fail", "0.5", "OCR misread", "", ""])
        writer.writerow(["r2", "test-0002", "B", "fail", "0.0", "", "", ""])
    (tmp_path / FAILURE_EXAMPLES).write_text(
        json.dumps(
            {
                "variant": "A",
                "case_id": "test-0001",
                "gold": {"total": "1"},
                "prediction": {"total": "7"},
            }
        )
        + "\nnot json\n",
        encoding="utf-8",
    )
    view = load_failures(tmp_path)
    assert view.causes == [ALL, "OCR misread", "unlabelled"] and view.pipelines == [ALL, "A", "B"]
    assert len(filter_failures(view, ALL, ALL)) == 2
    only_a = filter_failures(view, "OCR misread", "A")
    assert only_a == [["A", "test-0001", "OCR misread", "0.5", ""]]
    gold, predicted = failure_detail(tmp_path, only_a, 0)
    assert '"total": "1"' in gold and '"total": "7"' in predicted
    assert failure_detail(tmp_path, filter_failures(view, ALL, "B"), 0)[1] == (
        "No stored example for this row."
    )
    assert failure_detail(tmp_path, only_a, 5) == ("", "")


def test_failures_empty_state(tmp_path: Path) -> None:
    view = load_failures(tmp_path)
    assert view.rows == [] and "M3.1" in view.note


def test_cached_replay_does_not_pass_for_model_latency() -> None:
    view = column_view("A", result(cache_hit=True))
    assert "model 2.1 s" not in view.metadata and "validate" not in view.metadata
    assert "replayed from cache" in view.metadata and "OCR 1.6 s" in view.metadata
