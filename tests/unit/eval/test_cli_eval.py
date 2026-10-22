"""M0.4 acceptance through the CLI: a 20-case toy run completes, resumes after an interruption
and appears in ``reports/results.csv``; plus import, failures and error exits."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from receipt_extractor.cli import EXIT_INTERRUPTED, EXIT_PAUSED, app
from receipt_extractor.eval import tasks as tasks_module
from receipt_extractor.eval.records import RESULTS_FILENAME
from receipt_extractor.eval.toy import ToyParams, ToyTask
from receipt_extractor.llm.errors import BudgetExhaustedError

from .conftest import base_config
from .test_import_report import fake_predictions, write_kaggle_output

runner = CliRunner()
WriteConfig = Callable[..., Path]


def _run_dir(project: Path) -> Path:
    (run_dir,) = (project / "runs").iterdir()
    return run_dir


def test_toy_run_resumes_after_interruption_and_is_reported(
    project: Path, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(base_config(params={"error_rate": 0.2, "raise_ids": ["toy-00007"]}))

    # First attempt: Ctrl+C after 7 cases.
    class InterruptingToy(ToyTask):
        calls = 0

        async def __call__(self, case, ctx):  # type: ignore[no-untyped-def]
            InterruptingToy.calls += 1
            if InterruptingToy.calls > 7:
                raise KeyboardInterrupt
            return await super().__call__(case, ctx)

    real_toy = tasks_module.get_task_factory("toy")
    monkeypatch.setitem(
        tasks_module._REGISTRY,
        "toy",
        lambda params: InterruptingToy(ToyParams.model_validate(dict(params))),
    )
    first = runner.invoke(app, ["eval", "run", "--config", str(config), "--concurrency", "1"])
    assert first.exit_code == EXIT_INTERRUPTED, first.output
    assert "re-run the same command to resume" in first.output
    lines = (_run_dir(project) / RESULTS_FILENAME).read_text().splitlines()
    assert len(lines) == 7

    # Second attempt with the real task resumes the same run id.
    monkeypatch.setitem(tasks_module._REGISTRY, "toy", real_toy)
    second = runner.invoke(app, ["eval", "run", "--config", str(config)])
    assert second.exit_code == 0, second.output
    assert "20/20 done (13 this time, 1 errors)" in second.output
    run_dir = _run_dir(project)
    ids = [
        json.loads(line)["case_id"]
        for line in (run_dir / RESULTS_FILENAME).read_text().splitlines()
    ]
    assert len(ids) == len(set(ids)) == 20

    report = runner.invoke(app, ["report", "build"])
    assert report.exit_code == 0, report.output
    with (project / "reports" / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["variant"], r["run_id"], r["sample_size"]) for r in rows] == [
        ("toy", run_dir.name, "20")
    ]

    failures = runner.invoke(app, ["report", "failures"])
    assert failures.exit_code == 0, failures.output
    assert (project / "reports" / "failures.csv").is_file()


def test_paused_run_exits_with_paused_code(
    project: Path, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BudgetToy(ToyTask):
        async def __call__(self, case, ctx):  # type: ignore[no-untyped-def]
            raise BudgetExhaustedError("gemini-flash", "request", 20, 20)

    monkeypatch.setitem(tasks_module._REGISTRY, "toy", lambda params: BudgetToy(ToyParams()))
    result = runner.invoke(app, ["eval", "run", "--config", str(write_config(base_config()))])
    assert result.exit_code == EXIT_PAUSED
    assert "paused" in result.output


def test_import_predictions_command(project: Path) -> None:
    source = write_kaggle_output(project, fake_predictions())
    result = runner.invoke(app, ["eval", "import-predictions", str(source)])
    assert result.exit_code == 0, result.output
    assert "status       completed" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["eval", "run", "--config", "../outside.yaml"],
        ["eval", "run", "--config", "configs/missing.yaml"],
        ["eval", "import-predictions", "../outside"],
        ["report", "build", "--out", "../outside"],
        ["report", "failures", "--runs-dir", "data"],
    ],
)
def test_cli_errors_are_one_line_and_exit_1(project: Path, args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert result.output.startswith("error: ")
    assert "Traceback" not in result.output


def test_unknown_task_is_reported(project: Path, write_config: WriteConfig) -> None:
    config = write_config(base_config(task="nope"))
    result = runner.invoke(app, ["eval", "run", "--config", str(config)])
    assert result.exit_code == 1
    assert "unknown task 'nope'" in result.output
