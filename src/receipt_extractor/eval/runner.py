"""The evaluation runner (TRD §S4, APPFLOW §S1).

``prepare_run`` resolves a config into a :class:`PreparedRun` (dataset opened, sample
selected, run id computed). ``execute_run`` then works through the cases:

- **Bounded concurrency.** A producer feeds case ids into a queue of size ``concurrency`` and
  ``concurrency`` workers load, run and score one case each, so at most ``2 * concurrency``
  cases are in memory at once whatever the dataset size.
- **Streamed results.** Every finished case is appended to ``results.jsonl`` and flushed
  immediately; nothing is accumulated.
- **Resume.** Case ids already in ``results.jsonl`` are skipped; a partial last line left by a
  crash is cut first. Re-running the same command continues the same run id.
- **Errors.** Any exception from a case becomes a ``status = error`` row; the run continues.
- **Pause.** :class:`~receipt_extractor.llm.BudgetExhaustedError` stops new cases (no row is
  written for the case that hit it) and the run returns ``paused``; re-run to resume.
- **Completion.** When every selected case has a row, ``summary.json`` is written.

A per-run OS file lock stops two processes from appending to the same run.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from receipt_extractor.eval.config import (
    RunConfig,
    compute_run_id,
    load_run_config,
    normalise_paths,
)
from receipt_extractor.eval.datasets import Dataset, open_dataset
from receipt_extractor.eval.errors import RunConfigError, RunCorruptError
from receipt_extractor.eval.io import (
    JSONLAppender,
    RunLock,
    atomic_write_text,
    canonical_json,
    iter_jsonl,
    read_json_object,
    repair_tail,
)
from receipt_extractor.eval.metrics import Metric, case_details, get_metric
from receipt_extractor.eval.paths import RUNS_DIR, confine
from receipt_extractor.eval.records import (
    LOCK_FILENAME,
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
    CaseStatus,
    DatasetInfo,
    KaggleInfo,
    Manifest,
    ResultRow,
    Summary,
)
from receipt_extractor.eval.summary import build_manifest, build_summary
from receipt_extractor.eval.tasks import CaseContext, CaseOutput, CaseTask
from receipt_extractor.llm.errors import BudgetExhaustedError, sanitize_error_text
from receipt_extractor.llm.sinks import LLM_CALLS_FILENAME

logger = logging.getLogger(__name__)

RunStatus = Literal["completed", "paused"]
_RUN_UUID_NAMESPACE = uuid.UUID("6f1c1e0e-4f55-4c0b-9a51-3f0f6b1d2a10")


@dataclass(frozen=True)
class PreparedRun:
    config: RunConfig
    root: Path
    run_id: str
    run_dir: Path
    dataset: Dataset
    dataset_info: DatasetInfo
    case_ids: tuple[str, ...]


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    run_id: str
    run_dir: Path
    total: int
    previously_done: int
    executed: int
    errors: int
    paused_reason: str | None = None
    summary: Summary | None = None


def select_case_ids(all_ids: Sequence[str], config: RunConfig) -> list[str]:
    """Apply ``config.sample`` to the dataset's ids (fixed seed → fixed sample)."""
    sample = config.sample
    if sample.ids is not None:
        known = set(all_ids)
        missing = [case_id for case_id in sample.ids if case_id not in known]
        if missing:
            raise RunConfigError(
                f"{len(missing)} sample id(s) are not in the dataset, e.g. {missing[0]!r}"
            )
        return list(sample.ids)
    if sample.size is not None:
        if sample.size > len(all_ids):
            raise RunConfigError(
                f"sample.size {sample.size} exceeds the dataset's {len(all_ids)} cases"
            )
        rng = random.Random(sample.seed)  # noqa: S311 - sampling, not crypto
        chosen = set(rng.sample(sorted(all_ids), sample.size))
        return [case_id for case_id in all_ids if case_id in chosen]
    return list(all_ids)


def prepare_config(config: RunConfig, root: Path) -> PreparedRun:
    """Resolve an already validated config (also used by the prediction importer)."""
    config = normalise_paths(config, root)
    for name in config.metrics:
        get_metric(name)
    runs_root = confine(config.runs_dir, root, [RUNS_DIR])
    dataset = open_dataset(config.dataset, root)
    case_ids = tuple(select_case_ids(dataset.case_ids(), config))
    manifest_hash = dataset.manifest_hash()
    run_id = compute_run_id(config, manifest_hash)
    info = DatasetInfo(
        name=dataset.name,
        revision=dataset.revision,
        split=dataset.split,
        manifest_hash=manifest_hash,
        ids_hash=hashlib.sha256(canonical_json(case_ids).encode("utf-8")).hexdigest(),
        n_cases=len(case_ids),
    )
    return PreparedRun(
        config=config,
        root=root,
        run_id=run_id,
        run_dir=runs_root / run_id,
        dataset=dataset,
        dataset_info=info,
        case_ids=case_ids,
    )


def prepare_run(config_path: str | Path, root: Path) -> PreparedRun:
    return prepare_config(load_run_config(config_path, root), root)


def score_case(
    prediction: Any, gold: Any, config: RunConfig, metrics: Mapping[str, Metric]
) -> tuple[CaseStatus, float | None, dict[str, Any]]:
    """Pass/fail against ``pass_threshold``, the primary score and the scorer's details.

    The details are the metrics' ``explain`` entries plus ``metrics{name: value}``; they
    override same-named entries a task or an import supplies, so the scorer has the last word.
    """
    values = {name: metric.compute(prediction, gold) for name, metric in metrics.items()}
    score = values[config.primary_metric]
    status: CaseStatus = "pass" if score is not None and score >= config.pass_threshold else "fail"
    return status, score, {**case_details(prediction, gold, metrics), "metrics": values}


def error_message(exc: BaseException) -> str:
    """Exception type plus sanitised message — no traceback, no secrets."""
    text = sanitize_error_text(str(exc))
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


@dataclass
class RunSession:
    """An open run directory: lock held, manifest present, results ready to append."""

    prepared: PreparedRun
    manifest: Manifest
    finished: set[str]
    appender: JSONLAppender
    executed: int = 0
    errors: int = 0
    _metrics: dict[str, Metric] = field(default_factory=dict)

    @property
    def results_path(self) -> Path:
        return self.prepared.run_dir / RESULTS_FILENAME

    @property
    def metrics(self) -> dict[str, Metric]:
        if not self._metrics:
            self._metrics = {name: get_metric(name) for name in self.prepared.config.metrics}
        return self._metrics

    def pending(self) -> Iterator[str]:
        return (cid for cid in self.prepared.case_ids if cid not in self.finished)

    async def append(self, row: ResultRow) -> None:
        await self.appender.write_line(row.model_dump_json())
        self.finished.add(row.case_id)
        self.executed += 1
        if row.status == "error":
            self.errors += 1

    def finalize(self) -> Summary:
        """Write ``summary.json`` (only valid once every selected case has a row)."""
        prepared = self.prepared
        summary_path = prepared.run_dir / SUMMARY_FILENAME
        if self.executed == 0 and summary_path.is_file():
            try:
                return Summary.model_validate(read_json_object(summary_path))
            except ValidationError:
                logger.warning("summary.json of %s is invalid; rebuilding it", prepared.run_id)
        summary = build_summary(
            self.results_path,
            run_id=prepared.run_id,
            config=prepared.config,
            dataset_name=prepared.dataset_info.name,
            started_at=self.manifest.started_at,
            finished_at=datetime.now(UTC),
        )
        atomic_write_text(summary_path, summary.model_dump_json(indent=2, by_alias=True) + "\n")
        return summary

    def outcome(
        self, status: RunStatus, summary: Summary | None, reason: str | None = None
    ) -> RunOutcome:
        return RunOutcome(
            status=status,
            run_id=self.prepared.run_id,
            run_dir=self.prepared.run_dir,
            total=len(self.prepared.case_ids),
            previously_done=len(self.finished) - self.executed,
            executed=self.executed,
            errors=self.errors,
            paused_reason=reason,
            summary=summary,
        )


def _finished_ids(
    results_path: Path,
    selected: Iterable[str],
    *,
    retry_errors: bool = False,
    rerun: frozenset[str] = frozenset(),
) -> set[str]:
    """Case ids that have a row. With ``retry_errors``, a case whose *last* row is an error is
    not finished: it runs again and its new row is appended (rows are never rewritten; every
    reader takes the last row per case). ``rerun`` names cases that run again whatever their
    last row says, the same way."""
    wanted = set(selected)
    last_status: dict[str, object] = {}
    if not results_path.is_file():
        return set()
    for lineno, obj in iter_jsonl(results_path):
        case_id = obj.get("case_id")
        if not isinstance(case_id, str):
            raise RunCorruptError(f"{results_path.name}:{lineno}: row has no case_id")
        if case_id in wanted:
            last_status[case_id] = obj.get("status")
    if retry_errors:
        return {
            cid for cid, status in last_status.items() if status != "error" and cid not in rerun
        }
    return set(last_status) - rerun


def _load_or_write_manifest(prepared: PreparedRun, kaggle: KaggleInfo | None) -> Manifest:
    path = prepared.run_dir / MANIFEST_FILENAME
    if path.is_file():
        try:
            manifest = Manifest.model_validate(read_json_object(path))
        except ValidationError:
            raise RunCorruptError(f"{prepared.run_id}/{MANIFEST_FILENAME} is invalid") from None
        if manifest.run_id != prepared.run_id:
            raise RunCorruptError(f"{prepared.run_id}/{MANIFEST_FILENAME} names another run")
        return manifest
    manifest = build_manifest(
        run_id=prepared.run_id,
        config=prepared.config,
        dataset=prepared.dataset_info,
        root=prepared.root,
        started_at=datetime.now(UTC),
        kaggle=kaggle,
    )
    atomic_write_text(path, manifest.model_dump_json(indent=2) + "\n")
    return manifest


@contextmanager
def open_session(
    prepared: PreparedRun,
    *,
    kaggle: KaggleInfo | None = None,
    retry_errors: bool = False,
    rerun_cases: frozenset[str] = frozenset(),
) -> Iterator[RunSession]:
    unknown = rerun_cases - set(prepared.case_ids)
    if unknown:
        raise RunConfigError(f"--rerun-case: not in this run: {', '.join(sorted(unknown))}")
    prepared.run_dir.mkdir(parents=True, exist_ok=True)
    with RunLock(prepared.run_dir / LOCK_FILENAME):
        manifest = _load_or_write_manifest(prepared, kaggle)
        results_path = prepared.run_dir / RESULTS_FILENAME
        if repair_tail(results_path):
            logger.warning("run %s: removed a partial last line after a crash", prepared.run_id)
        finished = _finished_ids(
            results_path, prepared.case_ids, retry_errors=retry_errors, rerun=rerun_cases
        )
        appender = JSONLAppender(results_path)
        try:
            yield RunSession(
                prepared=prepared, manifest=manifest, finished=finished, appender=appender
            )
        finally:
            appender.close()


async def execute_run(
    prepared: PreparedRun,
    task: CaseTask,
    *,
    concurrency: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
    retry_errors: bool = False,
    rerun_cases: frozenset[str] = frozenset(),
) -> RunOutcome:
    config = prepared.config
    workers = concurrency or config.concurrency
    run_uuid = uuid.uuid5(_RUN_UUID_NAMESPACE, prepared.run_id)
    pause = asyncio.Event()
    pause_reason: list[str] = []

    with open_session(prepared, retry_errors=retry_errors, rerun_cases=rerun_cases) as session:
        metrics = session.metrics

        async def run_case(case_id: str) -> ResultRow | None:
            start = clock()
            gold: Any = None
            try:
                case = await asyncio.to_thread(prepared.dataset.load_case, case_id)
                gold = to_jsonable_python(case.gold)
                ctx = CaseContext(
                    run_id=prepared.run_id,
                    run_uuid=run_uuid,
                    run_dir=prepared.run_dir,
                    llm_calls_path=prepared.run_dir / LLM_CALLS_FILENAME,
                    case_id=case_id,
                    seed=config.seed,
                    root=prepared.root,
                )
                async with asyncio.timeout(config.case_timeout_s):
                    output = await task(case, ctx)
                if not isinstance(output, CaseOutput):
                    raise TypeError("task must return a CaseOutput")
                prediction = to_jsonable_python(output.prediction)
                status, score, scored = score_case(prediction, gold, config, metrics)
                return ResultRow(
                    case_id=case_id,
                    status=status,
                    score=score,
                    prediction=prediction,
                    gold=gold,
                    details={**to_jsonable_python(output.details), **scored},
                    latency_ms=(latency := _elapsed_ms(clock, start)),
                    wait_ms=min(output.wait_ms, latency),
                    tokens_in=output.tokens_in,
                    tokens_out=output.tokens_out,
                    list_cost_usd=output.list_cost_usd,
                )
            except BudgetExhaustedError as exc:
                pause_reason.append(str(exc))
                pause.set()
                return None
            except TimeoutError:
                message = f"TimeoutError: case exceeded case_timeout_s={config.case_timeout_s:g}"
                return _error_row(case_id, gold, message, _elapsed_ms(clock, start))
            except Exception as exc:  # every case failure becomes an error row
                logger.warning("case %s failed: %s", case_id, type(exc).__name__)
                return _error_row(case_id, gold, error_message(exc), _elapsed_ms(clock, start))

        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=workers)

        async def producer() -> None:
            for case_id in session.pending():
                if pause.is_set():
                    break
                await queue.put(case_id)
            # Not in `finally`: on cancellation the workers are cancelled too, and a put into
            # a full queue nobody drains would never return.
            for _ in range(workers):
                await queue.put(None)

        async def worker() -> None:
            while (case_id := await queue.get()) is not None:
                if pause.is_set():
                    continue
                row = await run_case(case_id)
                if row is not None:
                    await session.append(row)

        async with asyncio.TaskGroup() as group:
            group.create_task(producer())
            for _ in range(workers):
                group.create_task(worker())

        if pause.is_set():
            return session.outcome("paused", None, pause_reason[0])
        return session.outcome("completed", session.finalize())


def _elapsed_ms(clock: Callable[[], float], start: float) -> int:
    return max(0, round((clock() - start) * 1000))


def _error_row(case_id: str, gold: Any, message: str, latency_ms: int) -> ResultRow:
    return ResultRow(
        case_id=case_id,
        status="error",
        score=None,
        prediction=None,
        gold=gold,
        details={"error": message},
        latency_ms=latency_ms,
        tokens_in=0,
        tokens_out=0,
        list_cost_usd=Decimal(0),
    )
