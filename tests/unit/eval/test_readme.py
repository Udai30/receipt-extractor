"""README generation from reports/ (no hand-typed numbers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from receipt_extractor.eval.errors import RunCorruptError
from receipt_extractor.eval.readme import failures_table, results_table, run_model, update_readme

RESULTS = (
    "variant,dataset,subset,run_id,sample_size,primary_metric,value,ci_low,ci_high,p50_ms,p95_ms,"
    "list_cost_usd_per_1k,reconcile,valid_json\n"
    "B,cord-v2,test,B-1,100,field_f1,0.9572,0.9305,0.9782,4142,17049,0.975087,0.8500,1.0000\n"
    "gold,cord-v2,test,gold-1,100,field_f1,1.0,1.0,1.0,8,12,0.0,0.84,1.0\n"
    "A-naive,cord-v2,test,N-1,100,field_f1,0.82,0.7653,0.8702,2368,8364,0.352282,0.59,0.94\n"
    "A,cord-v2,validation,A-v,10,field_f1,0.9,0.8,1.0,1,2,0.1,0.9,1.0\n"
)
FAILURES = (
    "run_id,case_id,variant,status,score,failure_label,note,fixed_by\n"
    "N-1,test-0001,A-naive,fail,0.5,other,x,\n"
    "N-1,test-0002,A-naive,fail,0.5,ocr_misread,x,\n"
    "N-1,test-0003,A-naive,fail,0.5,other,x,\n"
    "B-1,test-0001,B,fail,0.5,hallucinated_item,x,\n"
    "B-1,test-0004,B,fail,0.5,,x,\n"
)


def test_results_table_lists_test_rows_in_fixed_order_without_gold() -> None:
    table = results_table(RESULTS, {"B-1": "gemini-flash-lite", "N-1": "groq-oss-20b"})
    lines = table.splitlines()
    assert [ln.split("|")[1].strip() for ln in lines[2:]] == ["A-naive", "B"]  # no gold, no val
    assert "0.8200 [0.7653, 0.8702]" in lines[2] and "groq-oss-20b" in lines[2]
    assert "2.4 s / 8.4 s" in lines[2] and "$0.3523" in lines[2] and "`N-1`" in lines[2]
    assert "0.9572 [0.9305, 0.9782]" in lines[3] and "4.1 s / 17.0 s" in lines[3]


def test_failures_table_counts_by_label_and_variant() -> None:
    table = failures_table(FAILURES)
    rows = {
        ln.split("|")[1].strip(): [c.strip() for c in ln.split("|")[2:-1]]
        for ln in table.splitlines()[2:]
    }
    assert rows["other"] == ["2", "0"]
    assert rows["ocr_misread"] == ["1", "0"]
    assert rows["hallucinated_item"] == ["0", "1"]
    assert rows["unlabelled"] == ["0", "1"]
    assert rows["**Total**"] == ["3", "2"]


def test_run_model_reads_the_manifest_and_tolerates_gaps(tmp_path: Path) -> None:
    (tmp_path / "R-1").mkdir()
    (tmp_path / "R-1" / "manifest.json").write_text(
        json.dumps({"resolved_config": {"params": {"model": "groq-oss-20b"}}}), encoding="utf-8"
    )
    (tmp_path / "R-2").mkdir()
    (tmp_path / "R-2" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert run_model(tmp_path, "R-1") == "groq-oss-20b"
    assert run_model(tmp_path, "R-2") == "—"
    assert run_model(tmp_path, "missing") == "—"


def _readme(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "README.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_update_readme_rewrites_only_the_marked_blocks_and_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "results.csv").write_text(RESULTS, encoding="utf-8")
    (tmp_path / "failures.csv").write_text(FAILURES, encoding="utf-8")
    readme = _readme(
        tmp_path,
        "intro\n<!-- results:start -->\nold\n<!-- results:end -->\nmid\n"
        "<!-- failures:start -->\nold\n<!-- failures:end -->\nend\n",
    )
    written = update_readme(readme, tmp_path / "results.csv", tmp_path / "failures.csv", tmp_path)
    text = readme.read_text(encoding="utf-8")
    assert written == ["results", "failures"]
    assert text.startswith("intro\n") and "\nmid\n" in text and text.endswith("end\n")
    assert "old" not in text and "| A-naive |" in text and "| **Total** |" in text

    update_readme(readme, tmp_path / "results.csv", tmp_path / "failures.csv", tmp_path)
    assert readme.read_text(encoding="utf-8") == text


def test_update_readme_fails_loudly_when_a_marker_is_missing(tmp_path: Path) -> None:
    (tmp_path / "results.csv").write_text(RESULTS, encoding="utf-8")
    readme = _readme(tmp_path, "no markers here\n")
    with pytest.raises(RunCorruptError, match="results:start"):
        update_readme(readme, tmp_path / "results.csv", None, tmp_path)
    assert readme.read_text(encoding="utf-8") == "no markers here\n"
