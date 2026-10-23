"""The ``pipeline_a`` and ``pipeline_b`` eval tasks: params, image loading checks, runner passes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.eval.config import RunConfig
from receipt_extractor.eval.datasets import Case
from receipt_extractor.eval.errors import DatasetError, PathConfinementError, RunConfigError
from receipt_extractor.eval.records import RESULTS_FILENAME, SUMMARY_FILENAME, ResultRow, Summary
from receipt_extractor.eval.runner import execute_run, prepare_config
from receipt_extractor.eval.tasks import CaseContext, build_task
from receipt_extractor.llm.client import LLMClient
from receipt_extractor.llm.fake import FakeProvider, FakeReply
from receipt_extractor.llm.sinks import LLM_CALLS_FILENAME, JSONLSink
from receipt_extractor.pipelines.images import ModelImageSettings
from receipt_extractor.pipelines.ocr import OcrSettings, OcrStage
from receipt_extractor.pipelines.tasks import (
    PipelineAParams,
    PipelineATask,
    PipelineBParams,
    PipelineBTask,
)
from tests.unit.pipelines.conftest import RECEIPT_BOXES, RECEIPT_TEXT, FakeOcrEngine, png_bytes
from tests.unit.pipelines.test_pipeline_a import INVALID, VALID

Factory = Callable[..., LLMClient]

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
METRICS = ["field_f1", "items_f1", "total_f1", "valid_json"]


@pytest.fixture
def project(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    return tmp_path.resolve()


def write_image(project: Path, name: str = "r.png") -> tuple[str, str]:
    data = png_bytes()
    path = project / "data" / "img" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"data/img/{name}", hashlib.sha256(data).hexdigest()


def ctx(project: Path, case_id: str = "c-1") -> CaseContext:
    import uuid  # noqa: PLC0415

    run_dir = project / "runs" / "x"
    return CaseContext(
        run_id="x",
        run_uuid=uuid.uuid4(),
        run_dir=run_dir,
        llm_calls_path=run_dir / LLM_CALLS_FILENAME,
        case_id=case_id,
        seed=0,
        root=project,
    )


def make_task(
    make_client: Factory, provider: FakeProvider, mode: str = "naive", **params: Any
) -> PipelineATask:
    def client_factory(context: CaseContext) -> LLMClient:
        return make_client(provider, sinks=[JSONLSink(context.llm_calls_path)])

    def ocr_factory(settings: OcrSettings) -> OcrStage:
        return OcrStage(FakeOcrEngine(RECEIPT_BOXES))

    return PipelineATask(
        PipelineAParams.model_validate({"mode": mode, "model": "test-chat", **params}),
        client_factory=client_factory,
        ocr_factory=ocr_factory,
    )


# --- params ------------------------------------------------------------------------------


def test_registered_and_params_validated() -> None:
    task = build_task("pipeline_a", {"mode": "engineered"})
    assert isinstance(task, PipelineATask)
    assert task.params.model == "groq-oss-20b"
    assert task.params.ocr == OcrSettings()


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"mode": "clever"},
        {"mode": "naive", "prompt_version": "a-v0"},
        {"mode": "naive", "model": "groq/openai/gpt-oss-20b"},  # model strings stay in yaml
        {"mode": "naive", "temperature": 1},
        {"mode": "naive", "ocr": {"threads": 0}},
    ],
)
def test_bad_params_rejected(params: dict[str, Any]) -> None:
    with pytest.raises(RunConfigError, match="pipeline_a"):
        build_task("pipeline_a", params)


# --- image loading -----------------------------------------------------------------------


async def test_task_runs_pipeline_and_keeps_ocr_text_in_details(
    project: Path, make_client: Factory
) -> None:
    path, sha = write_image(project)
    provider = FakeProvider([FakeReply(text=VALID, tokens_in=800, tokens_out=200)])
    output = await make_task(make_client, provider)(
        Case(case_id="c-1", input={"image": path, "image_sha256": sha}, gold=GOLD), ctx(project)
    )
    assert output.prediction.receipt is not None
    assert output.details == {"ocr_text": RECEIPT_TEXT}
    assert (output.tokens_in, output.tokens_out) == (800, 200)
    assert provider.calls[0].messages[1]["content"].count(RECEIPT_TEXT) == 1


async def test_tampered_image_is_rejected(project: Path, make_client: Factory) -> None:
    path, _ = write_image(project)
    provider = FakeProvider()
    with pytest.raises(DatasetError, match="SHA-256"):
        await make_task(make_client, provider)(
            Case(case_id="c-1", input={"image": path, "image_sha256": "0" * 64}, gold=GOLD),
            ctx(project),
        )
    assert provider.calls == []


@pytest.mark.parametrize("path", ["../outside.png", "configs/x.png", "C:/Windows/win.ini"])
async def test_image_path_is_confined_to_data(
    project: Path, make_client: Factory, path: str
) -> None:
    with pytest.raises(PathConfinementError):
        await make_task(make_client, FakeProvider())(
            Case(case_id="c-1", input={"image": path, "image_sha256": "0" * 64}, gold=GOLD),
            ctx(project),
        )


async def test_malformed_case_input_is_rejected(project: Path, make_client: Factory) -> None:
    with pytest.raises(DatasetError, match="image_sha256"):
        await make_task(make_client, FakeProvider())(
            Case(case_id="c-1", input={"image": "data/x.png"}, gold=GOLD), ctx(project)
        )


# --- through the runner ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "replies", "expected_status"),
    [
        ("naive", [VALID], "pass"),
        ("naive", [INVALID], "fail"),
        ("engineered", [INVALID, VALID], "pass"),
    ],
)
async def test_runner_scores_pipeline_output(
    project: Path,
    make_client: Factory,
    mode: str,
    replies: list[str],
    expected_status: str,
) -> None:
    path, sha = write_image(project)
    dataset = project / "data" / "cases.jsonl"
    case = {"case_id": "r-1", "input": {"image": path, "image_sha256": sha}, "gold": GOLD}
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")
    config = RunConfig.model_validate(
        {
            "variant": "A-naive" if mode == "naive" else "A",
            "task": "pipeline_a",
            "params": {"mode": mode, "model": "test-chat"},
            "dataset": {"kind": "jsonl", "name": "r", "split": "validation", "path": str(dataset)},
            "models": ["test-chat"],
            "metrics": METRICS,
            "primary_metric": "field_f1",
            "concurrency": 1,
        }
    )
    prepared = prepare_config(config, project)
    provider = FakeProvider([FakeReply(text=t) for t in replies])
    outcome = await execute_run(prepared, make_task(make_client, provider, mode))
    assert outcome.status == "completed"

    lines = (prepared.run_dir / RESULTS_FILENAME).read_text(encoding="utf-8").splitlines()
    (row,) = [ResultRow.model_validate(json.loads(line)) for line in lines]
    assert row.status == expected_status
    assert row.details["ocr_text"] == RECEIPT_TEXT
    assert set(row.prediction["stage_latency_ms"]) == {
        "ocr",
        "prepare",
        "llm",
        "llm_wait",
        "validate",
    }
    assert "ocr_text" not in row.prediction
    assert row.details["field_scores"]["valid_json"] is (expected_status == "pass")
    calls = (prepared.run_dir / LLM_CALLS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(calls) == len(replies)
    assert all(json.loads(c)["case_id"] == "r-1" for c in calls)
    summary = Summary.model_validate_json((prepared.run_dir / SUMMARY_FILENAME).read_text())
    assert summary.metrics["valid_json"].value == (1.0 if expected_status == "pass" else 0.0)


# --- pipeline_b ----------------------------------------------------------------------------


def make_task_b(make_client: Factory, provider: FakeProvider, **params: Any) -> PipelineBTask:
    def client_factory(context: CaseContext) -> LLMClient:
        return make_client(provider, sinks=[JSONLSink(context.llm_calls_path)])

    return PipelineBTask(
        PipelineBParams.model_validate({"model": "test-public", **params}),
        client_factory=client_factory,
    )


def test_pipeline_b_registered_with_defaults() -> None:
    task = build_task("pipeline_b", {})
    assert isinstance(task, PipelineBTask)
    assert task.params.model == "gemini-flash-lite"
    assert task.params.prompt_version == "b-v1"
    assert task.params.image == ModelImageSettings()


@pytest.mark.parametrize(
    "params",
    [
        {"prompt_version": "a-v3"},  # a text prompt is not a vision prompt
        {"model": "gemini/gemini-3.8-flash"},  # model strings stay in yaml
        {"mode": "naive"},
        {"image": {"max_side": 64}},
        {"image": {"format": "gif"}},
        {"max_tokens": 10},
    ],
)
def test_pipeline_b_bad_params_rejected(params: dict[str, Any]) -> None:
    with pytest.raises(RunConfigError, match="pipeline_b"):
        build_task("pipeline_b", params)


async def test_pipeline_b_task_sends_case_image_as_public_dataset(
    project: Path, make_client: Factory
) -> None:
    path, sha = write_image(project)
    provider = FakeProvider([FakeReply(text=VALID, image_tokens_in=258)])
    output = await make_task_b(make_client, provider)(
        Case(case_id="c-1", input={"image": path, "image_sha256": sha}, gold=GOLD), ctx(project)
    )
    assert output.prediction.pipeline == "B"
    assert output.prediction.receipt is not None
    assert output.prediction.image_tokens_in == 258
    assert output.details == {}  # no OCR text in B
    # The public_only alias accepted it, so the task declared the CORD origin.
    assert len(provider.calls) == 1


async def test_pipeline_b_task_rejects_tampered_image(project: Path, make_client: Factory) -> None:
    path, _ = write_image(project)
    provider = FakeProvider([FakeReply(text=VALID)])
    with pytest.raises(DatasetError, match="SHA-256"):
        await make_task_b(make_client, provider)(
            Case(case_id="c-1", input={"image": path, "image_sha256": "0" * 64}, gold=GOLD),
            ctx(project),
        )
    assert provider.calls == []


@pytest.mark.parametrize(
    ("replies", "expected_status", "attempts"),
    [([VALID], "pass", 1), ([INVALID, VALID], "pass", 2), ([INVALID] * 3, "fail", 3)],
)
async def test_runner_scores_pipeline_b_output(
    project: Path,
    make_client: Factory,
    replies: list[str],
    expected_status: str,
    attempts: int,
) -> None:
    path, sha = write_image(project)
    dataset = project / "data" / "cases.jsonl"
    case = {"case_id": "r-1", "input": {"image": path, "image_sha256": sha}, "gold": GOLD}
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")
    config = RunConfig.model_validate(
        {
            "variant": "B",
            "task": "pipeline_b",
            "params": {"model": "test-public"},
            "dataset": {"kind": "jsonl", "name": "r", "split": "validation", "path": str(dataset)},
            "models": ["test-public"],
            "metrics": METRICS,
            "primary_metric": "field_f1",
            "concurrency": 1,
        }
    )
    prepared = prepare_config(config, project)
    provider = FakeProvider([FakeReply(text=t, image_tokens_in=258) for t in replies])
    outcome = await execute_run(prepared, make_task_b(make_client, provider))
    assert outcome.status == "completed"

    lines = (prepared.run_dir / RESULTS_FILENAME).read_text(encoding="utf-8").splitlines()
    (row,) = [ResultRow.model_validate(json.loads(line)) for line in lines]
    assert row.status == expected_status
    assert row.prediction["attempts"] == attempts
    assert row.prediction["image_tokens_in"] == 258 * attempts
    assert row.prediction["stage_latency_ms"]["prepare"] is not None
    assert "ocr_text" not in row.details
    calls = [
        json.loads(c)
        for c in (prepared.run_dir / LLM_CALLS_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    assert len(calls) == len(replies)
    assert all(c["case_id"] == "r-1" and c["image_tokens_in"] == 258 for c in calls)


def test_pipeline_b_task_variant_and_timeout_params() -> None:
    task = build_task("pipeline_b", {"variant": "C-local", "timeout_s": 600})
    assert isinstance(task, PipelineBTask)
    assert (task.params.variant, task.params.timeout_s) == ("C-local", 600)
    for bad in ({"variant": "C local!"}, {"timeout_s": 0}, {"timeout_s": 601}):
        with pytest.raises(RunConfigError, match="pipeline_b"):
            build_task("pipeline_b", bad)


def test_local_run_config_is_valid() -> None:
    from receipt_extractor.eval.config import load_run_config  # noqa: PLC0415

    root = Path(__file__).resolve().parents[3]
    config = load_run_config(root / "configs" / "runs" / "c-local-val10.yaml", root)
    assert config.variant == "C-local" and config.task == "pipeline_b"
    task = build_task(config.task, config.params)
    assert isinstance(task, PipelineBTask)
    assert task.params.model == "ollama-qwen3-vl-4b" and task.params.variant == "C-local"


@pytest.mark.parametrize("task_name", ["pipeline_a", "pipeline_b"])
def test_retry_and_tolerance_params(task_name: str) -> None:
    base: dict[str, Any] = {"mode": "engineered"} if task_name == "pipeline_a" else {}
    task = build_task(task_name, base)
    assert task.params.max_repairs == 2  # type: ignore[attr-defined]
    assert str(task.params.reconcile_tolerance) == "0.005"  # type: ignore[attr-defined]
    tuned = build_task(task_name, {**base, "max_repairs": 1, "reconcile_tolerance": "0.01"})
    assert tuned.params.max_repairs == 1  # type: ignore[attr-defined]
    for bad in (
        {"max_repairs": 0},
        {"max_repairs": 3},
        {"reconcile_tolerance": "-0.1"},
        {"reconcile_tolerance": "0.5"},
    ):
        with pytest.raises(RunConfigError, match=task_name):
            build_task(task_name, {**base, **bad})
