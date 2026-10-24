"""Runner behaviour: completion, per-case errors, resume after a crash, pause, bounded memory."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from receipt_extractor.eval import runner as runner_module
from receipt_extractor.eval.datasets import Case
from receipt_extractor.eval.errors import RunConfigError, RunLockedError
from receipt_extractor.eval.records import (
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
    Manifest,
    Summary,
)
from receipt_extractor.eval.runner import PreparedRun, execute_run, open_session
from receipt_extractor.eval.tasks import CaseContext, CaseOutput, build_task
from receipt_extractor.llm.errors import BudgetExhaustedError

from .conftest import read_rows

Prepare = Callable[..., PreparedRun]


class SimulatedCrash(BaseException):
    """Not an ``Exception``: escapes case capture like a kill or Ctrl+C would."""


class AddTask:
    """Correct answers; optional hooks to raise, crash or exhaust the budget."""

    def __init__(
        self,
        *,
        raise_ids: frozenset[str] = frozenset(),
        crash_after: int | None = None,
        budget_after: int | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.raise_ids = raise_ids
        self.crash_after = crash_after
        self.budget_after = budget_after
        self.delay_s = delay_s
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, case: Case, ctx: CaseContext) -> CaseOutput:
        self.calls.append(case.case_id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay_s)
            if self.crash_after is not None and len(self.calls) > self.crash_after:
                raise SimulatedCrash
            if self.budget_after is not None and len(self.calls) > self.budget_after:
                raise BudgetExhaustedError("test-chat", "request", 3, 3)
            if case.case_id in self.raise_ids:
                raise RuntimeError("boom api_key=sk-THISISNOTAREALKEY123")
            return CaseOutput(prediction={"sum": case.input["a"] + case.input["b"]}, tokens_in=5)
        finally:
            self.in_flight -= 1


async def test_toy_run_over_20_cases_completes(prepare: Prepare) -> None:
    prepared = prepare(20)
    outcome = await execute_run(prepared, build_task("toy", {"error_rate": 0.2}))

    assert outcome.status == "completed"
    rows = read_rows(prepared.run_dir)
    assert sorted(r.case_id for r in rows) == list(prepared.case_ids)
    assert len(rows) == 20
    summary = Summary.model_validate_json((prepared.run_dir / SUMMARY_FILENAME).read_text())
    manifest = Manifest.model_validate_json((prepared.run_dir / MANIFEST_FILENAME).read_text())
    assert summary.sample_size == 20
    assert summary.metrics["exact_match"].value == outcome.summary.metrics["exact_match"].value  # type: ignore[union-attr]
    assert manifest.run_id == prepared.run_id == prepared.run_dir.name
    assert manifest.dataset.n_cases == 20
    assert "pydantic" in manifest.packages


async def test_case_exception_becomes_error_row_and_run_continues(prepare: Prepare) -> None:
    prepared = prepare(10)
    task = AddTask(raise_ids=frozenset({"toy-00003"}))
    outcome = await execute_run(prepared, task)

    rows = {r.case_id: r for r in read_rows(prepared.run_dir)}
    assert outcome.status == "completed"
    assert len(rows) == 10
    error = rows["toy-00003"]
    assert error.status == "error"
    assert error.score is None
    assert error.details["error"].startswith("RuntimeError: boom")
    assert "sk-THISISNOTAREALKEY123" not in error.details["error"]  # sanitised
    assert error.gold is not None
    assert all(r.status == "pass" for cid, r in rows.items() if cid != "toy-00003")
    assert outcome.summary is not None
    assert outcome.summary.n_errors == 1
    # Errors count as 0 for the metric, never silently dropped.
    assert outcome.summary.metrics["exact_match"].value == pytest.approx(0.9)


async def test_resume_skips_finished_ids_after_simulated_crash(prepare: Prepare) -> None:
    prepared = prepare(20)
    crashing = AddTask(crash_after=8)
    with pytest.raises(BaseExceptionGroup) as info:
        await execute_run(prepared, crashing, concurrency=1)
    assert info.group_contains(SimulatedCrash)

    done_before = {r.case_id for r in read_rows(prepared.run_dir)}
    assert len(done_before) == 8
    assert not (prepared.run_dir / SUMMARY_FILENAME).exists()

    resumed = AddTask()
    outcome = await execute_run(prepared, resumed)
    assert outcome.status == "completed"
    assert outcome.previously_done == 8
    assert outcome.executed == 12
    assert set(resumed.calls).isdisjoint(done_before)
    rows = read_rows(prepared.run_dir)
    assert len(rows) == 20
    assert len({r.case_id for r in rows}) == 20  # no duplicates


async def test_resume_cuts_partial_last_line(prepare: Prepare) -> None:
    prepared = prepare(5)
    await execute_run(prepared, AddTask())
    results = prepared.run_dir / RESULTS_FILENAME
    lines = results.read_text(encoding="utf-8").splitlines(keepends=True)
    # Simulate a crash mid-write: keep 3 whole lines plus half of the 4th.
    results.write_text("".join(lines[:3]) + lines[3][:20], encoding="utf-8", newline="")
    (prepared.run_dir / SUMMARY_FILENAME).unlink()

    resumed = AddTask()
    outcome = await execute_run(prepared, resumed)
    assert outcome.status == "completed"
    assert len(resumed.calls) == 2
    assert [json.loads(line)["case_id"] for line in results.read_text().splitlines()] == [
        r.case_id for r in read_rows(prepared.run_dir)
    ]
    assert len(read_rows(prepared.run_dir)) == 5


async def test_rerun_of_completed_run_executes_nothing(prepare: Prepare) -> None:
    prepared = prepare(5)
    first = await execute_run(prepared, AddTask())
    again = AddTask()
    second = await execute_run(prepared, again)
    assert again.calls == []
    assert second.summary == first.summary


async def test_budget_exhausted_pauses_then_resumes(prepare: Prepare) -> None:
    prepared = prepare(10)
    outcome = await execute_run(prepared, AddTask(budget_after=4), concurrency=1)
    assert outcome.status == "paused"
    assert outcome.paused_reason is not None
    assert "budget" in outcome.paused_reason
    assert len(read_rows(prepared.run_dir)) == 4  # the case that hit the budget has no row
    assert not (prepared.run_dir / SUMMARY_FILENAME).exists()

    resumed = await execute_run(prepared, AddTask())
    assert resumed.status == "completed"
    assert resumed.executed == 6
    assert all(r.status == "pass" for r in read_rows(prepared.run_dir))


async def test_concurrency_is_bounded(prepare: Prepare) -> None:
    prepared = prepare(30)
    task = AddTask(delay_s=0.005)
    await execute_run(prepared, task, concurrency=3)
    assert task.max_in_flight == 3


async def test_memory_is_bounded_by_concurrency(
    prepare: Prepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cases loaded but not yet written never exceed 2 x concurrency (queue + workers)."""
    prepared = prepare(200)
    concurrency = 4
    loaded = 0
    written = 0
    peak = 0
    original_load = prepared.dataset.load_case

    def counting_load(case_id: str) -> Case:
        nonlocal loaded, peak
        loaded += 1
        peak = max(peak, loaded - written)
        return original_load(case_id)

    monkeypatch.setattr(prepared.dataset, "load_case", counting_load)
    original_append = runner_module.RunSession.append

    async def counting_append(self: runner_module.RunSession, row: object) -> None:
        nonlocal written
        await original_append(self, row)  # type: ignore[arg-type]
        written += 1

    monkeypatch.setattr(runner_module.RunSession, "append", counting_append)
    await execute_run(prepared, AddTask(delay_s=0.001), concurrency=concurrency)
    assert written == 200
    assert peak <= 2 * concurrency


async def test_case_timeout_becomes_error(prepare: Prepare) -> None:
    prepared = prepare(3, case_timeout_s=0.05)
    outcome = await execute_run(prepared, AddTask(delay_s=1.0))
    assert outcome.errors == 3
    assert all("case_timeout_s" in r.details["error"] for r in read_rows(prepared.run_dir))


async def test_second_session_on_same_run_is_refused(prepare: Prepare) -> None:
    prepared = prepare(3)
    with open_session(prepared), pytest.raises(RunLockedError), open_session(prepared):
        pass


async def test_jsonl_dataset_run(project: Path, prepare: Prepare) -> None:
    data = project / "data" / "cases.jsonl"
    data.parent.mkdir()
    lines = [
        {"case_id": f"c{i}", "input": {"a": i, "b": 1}, "gold": {"sum": i + 1}} for i in range(6)
    ]
    data.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    prepared = prepare(
        dataset={"kind": "jsonl", "name": "cases", "split": "test", "path": "./data/cases.jsonl"},
        sample={"size": 4, "seed": 1},
    )
    assert prepared.config.dataset.path == "data/cases.jsonl"  # type: ignore[union-attr]
    outcome = await execute_run(prepared, AddTask())
    assert outcome.summary is not None
    assert outcome.summary.sample_size == 4
    assert outcome.summary.metrics["exact_match"].value == 1.0


# --- provider quota and retrying error rows (M2.3 incident, 2026-10-23) ----------------------


class QuotaTask(AddTask):
    """The provider refuses for its daily quota from the 4th case on."""

    async def __call__(self, case: Case, ctx: CaseContext) -> CaseOutput:
        from receipt_extractor.llm.errors import ProviderQuotaExhaustedError  # noqa: PLC0415

        if len(self.calls) >= 3:
            self.calls.append(case.case_id)
            raise ProviderQuotaExhaustedError("test-chat", 7200.0)
        return await super().__call__(case, ctx)


async def test_provider_quota_pauses_instead_of_writing_error_rows(prepare: Prepare) -> None:
    prepared = prepare(10)
    outcome = await execute_run(prepared, QuotaTask(), concurrency=1)

    assert outcome.status == "paused" and outcome.paused_reason is not None
    assert "provider daily quota" in outcome.paused_reason
    rows = read_rows(prepared.run_dir)
    assert len(rows) == 3 and all(r.status == "pass" for r in rows)  # no error rows


async def test_retry_errors_reruns_only_error_rows_and_last_row_wins(prepare: Prepare) -> None:
    prepared = prepare(6)
    await execute_run(prepared, AddTask(raise_ids=frozenset({"toy-00001", "toy-00004"})))

    plain = AddTask()
    resumed = await execute_run(prepared, plain)
    assert plain.calls == [] and resumed.executed == 0  # a normal resume keeps error rows

    retry = AddTask()
    outcome = await execute_run(prepared, retry, retry_errors=True)
    assert sorted(retry.calls) == ["toy-00001", "toy-00004"]
    rows = read_rows(prepared.run_dir)
    assert len(rows) == 8  # appended, nothing rewritten
    assert outcome.summary is not None and outcome.summary.n_errors == 0
    assert outcome.summary.metrics["exact_match"].value == pytest.approx(1.0)

    again = AddTask()
    await execute_run(prepared, again, retry_errors=True)
    assert again.calls == []  # nothing left to retry


async def test_rerun_case_reruns_named_cases_even_when_they_passed(prepare: Prepare) -> None:
    prepared = prepare(6)
    await execute_run(prepared, AddTask())

    rerun = AddTask()
    outcome = await execute_run(prepared, rerun, rerun_cases=frozenset({"toy-00002", "toy-00005"}))
    assert sorted(rerun.calls) == ["toy-00002", "toy-00005"]
    assert len(read_rows(prepared.run_dir)) == 8  # appended, nothing rewritten
    assert outcome.summary is not None and sum(outcome.summary.status_counts.values()) == 6

    quiet = AddTask()
    await execute_run(prepared, quiet)
    assert quiet.calls == []  # without the option a resume leaves them alone


async def test_rerun_case_rejects_an_id_outside_the_run(prepare: Prepare) -> None:
    prepared = prepare(3)
    with pytest.raises(RunConfigError, match="not in this run: toy-99999"):
        await execute_run(prepared, AddTask(), rerun_cases=frozenset({"toy-99999"}))
