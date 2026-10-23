"""The ``pipeline_a`` and ``pipeline_b`` eval tasks: run a pipeline on CORD cases (TRD §S4).

Params (validated, part of the run id):

- ``pipeline_a``: ``mode`` (``naive`` | ``engineered``), ``model`` (an alias from
  ``configs/models.yaml``), ``prompt_version``, ``max_tokens`` and ``ocr`` settings.
- ``pipeline_b``: ``model``, ``prompt_version``, ``max_tokens`` and ``image`` settings (the
  downscale applied before the vision model sees the receipt).

The LLM client is built on the first case with the disk cache ON (TRD §S3: eval runs re-run for
free) and the run's ``llm_calls.jsonl`` as its sink; the OCR engine is built on first use too.

Case images are public CORD data (``data_origin = public_dataset``). The image path comes from
the dataset manifest; it is confined to ``data/`` and its SHA-256 checked before use.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from receipt_extractor.core.extraction import ExtractionResult
from receipt_extractor.core.reconcile import REL_TOLERANCE
from receipt_extractor.eval.datasets import Case, describe_validation_error
from receipt_extractor.eval.errors import DatasetError, RunConfigError
from receipt_extractor.eval.paths import DATA_DIR, confine
from receipt_extractor.eval.tasks import CaseContext, CaseOutput, register_task
from receipt_extractor.llm import CallTags, JSONLSink, LLMClient
from receipt_extractor.pipelines import pipeline_a, pipeline_b
from receipt_extractor.pipelines.base import Pipeline
from receipt_extractor.pipelines.images import MAX_IMAGE_BYTES, ModelImageSettings
from receipt_extractor.pipelines.ocr import OcrSettings, OcrStage, RapidOcrEngine
from receipt_extractor.pipelines.pipeline_a import Mode, PipelineA
from receipt_extractor.pipelines.pipeline_b import PipelineB
from receipt_extractor.pipelines.prompts import (
    DEFAULT_PROMPT_VERSION,
    DEFAULT_VISION_PROMPT_VERSION,
    PROMPTS,
    VISION_PROMPTS,
)

__all__ = ["PipelineAParams", "PipelineATask", "PipelineBParams", "PipelineBTask"]

_ALIAS_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"

ClientFactory = Callable[[CaseContext], LLMClient]
OcrFactory = Callable[[OcrSettings], OcrStage]


class PipelineAParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Mode
    model: str = Field(default=pipeline_a.DEFAULT_MODEL_ALIAS, pattern=_ALIAS_PATTERN)
    prompt_version: str = DEFAULT_PROMPT_VERSION
    max_tokens: int = Field(default=pipeline_a.DEFAULT_MAX_TOKENS, ge=256, le=65536)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    max_repairs: int = Field(default=pipeline_a.DEFAULT_MAX_REPAIRS, ge=1, le=2)
    reconcile_tolerance: Decimal = Field(default=REL_TOLERANCE, ge=0, le=Decimal("0.1"))

    @field_validator("prompt_version")
    @classmethod
    def _known_prompt(cls, value: str) -> str:
        if value not in PROMPTS:
            raise ValueError(f"unknown prompt version (known: {', '.join(sorted(PROMPTS))})")
        return value


class PipelineBParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(default=pipeline_b.DEFAULT_MODEL_ALIAS, pattern=_ALIAS_PATTERN)
    prompt_version: str = DEFAULT_VISION_PROMPT_VERSION
    max_tokens: int = Field(default=pipeline_b.DEFAULT_MAX_TOKENS, ge=256, le=65536)
    image: ModelImageSettings = Field(default_factory=ModelImageSettings)
    # The variant name results are reported under (`B`; `C-local` for the local Ollama VLM).
    variant: str = Field(default=pipeline_b.VARIANT, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    timeout_s: float | None = Field(default=None, gt=0, le=600)
    max_repairs: int = Field(default=pipeline_a.DEFAULT_MAX_REPAIRS, ge=1, le=2)
    reconcile_tolerance: Decimal = Field(default=REL_TOLERANCE, ge=0, le=Decimal("0.1"))

    @field_validator("prompt_version")
    @classmethod
    def _known_prompt(cls, value: str) -> str:
        if value not in VISION_PROMPTS:
            known = ", ".join(sorted(VISION_PROMPTS))
            raise ValueError(f"unknown vision prompt version (known: {known})")
        return value


def _default_client(ctx: CaseContext) -> LLMClient:
    from receipt_extractor.config import get_settings  # noqa: PLC0415
    from receipt_extractor.llm import build_client  # noqa: PLC0415

    return build_client(get_settings(), cache=True, sinks=[JSONLSink(ctx.llm_calls_path)])


def _default_ocr(settings: OcrSettings) -> OcrStage:
    return OcrStage(RapidOcrEngine(settings))


class _PipelineTask:
    """Builds its pipeline once (on the first case) and runs it on each CORD case image."""

    def __init__(self, *, client_factory: ClientFactory) -> None:
        self._client_factory = client_factory
        self._pipeline: Pipeline | None = None
        self._lock = asyncio.Lock()

    def _build(self, ctx: CaseContext) -> Pipeline:
        raise NotImplementedError

    def _details(self, result: ExtractionResult) -> dict[str, Any]:
        return {}

    async def _get_pipeline(self, ctx: CaseContext) -> Pipeline:
        async with self._lock:
            if self._pipeline is None:
                self._pipeline = self._build(ctx)
            return self._pipeline

    async def __call__(self, case: Case, ctx: CaseContext) -> CaseOutput:
        image = await asyncio.to_thread(_read_case_image, case, ctx)
        pipeline = await self._get_pipeline(ctx)
        result = await pipeline.extract(
            image,
            origin="public_dataset",
            tags=CallTags(run_id=ctx.run_uuid, case_id=ctx.case_id),
        )
        return CaseOutput(
            prediction=result,
            details=self._details(result),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            list_cost_usd=result.list_cost_usd,
            wait_ms=result.stage_latency_ms.llm_wait or 0,
        )


class PipelineATask(_PipelineTask):
    def __init__(
        self,
        params: PipelineAParams,
        *,
        client_factory: ClientFactory = _default_client,
        ocr_factory: OcrFactory = _default_ocr,
    ) -> None:
        super().__init__(client_factory=client_factory)
        self.params = params
        self._ocr_factory = ocr_factory

    def _build(self, ctx: CaseContext) -> Pipeline:
        params = self.params
        return PipelineA(
            client=self._client_factory(ctx),
            ocr=self._ocr_factory(params.ocr),
            mode=params.mode,
            model_alias=params.model,
            prompt_version=params.prompt_version,
            max_tokens=params.max_tokens,
            seed=ctx.seed,
            max_repairs=params.max_repairs,
            reconcile_tolerance=params.reconcile_tolerance,
        )

    def _details(self, result: ExtractionResult) -> dict[str, Any]:
        return {"ocr_text": result.ocr_text}


class PipelineBTask(_PipelineTask):
    def __init__(
        self, params: PipelineBParams, *, client_factory: ClientFactory = _default_client
    ) -> None:
        super().__init__(client_factory=client_factory)
        self.params = params

    def _build(self, ctx: CaseContext) -> Pipeline:
        params = self.params
        return PipelineB(
            client=self._client_factory(ctx),
            model_alias=params.model,
            prompt_version=params.prompt_version,
            max_tokens=params.max_tokens,
            image=params.image,
            seed=ctx.seed,
            variant=params.variant,
            timeout_s=params.timeout_s,
            max_repairs=params.max_repairs,
            reconcile_tolerance=params.reconcile_tolerance,
        )


def _read_case_image(case: Case, ctx: CaseContext) -> bytes:
    spec = case.input if isinstance(case.input, dict) else {}
    relative, expected = spec.get("image"), spec.get("image_sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        raise DatasetError(f"case {case.case_id}: input needs 'image' and 'image_sha256'")
    path = confine(relative, ctx.root, [DATA_DIR])
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise DatasetError(f"case {case.case_id}: image file too large")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise DatasetError(f"case {case.case_id}: image does not match its manifest SHA-256")
    return data


def _factory_a(params: Mapping[str, Any]) -> PipelineATask:
    try:
        return PipelineATask(PipelineAParams.model_validate(dict(params)))
    except ValidationError as exc:
        raise RunConfigError(
            describe_validation_error(exc, "params of task 'pipeline_a'")
        ) from None


def _factory_b(params: Mapping[str, Any]) -> PipelineBTask:
    try:
        return PipelineBTask(PipelineBParams.model_validate(dict(params)))
    except ValidationError as exc:
        raise RunConfigError(
            describe_validation_error(exc, "params of task 'pipeline_b'")
        ) from None


register_task("pipeline_a", _factory_a)
register_task("pipeline_b", _factory_b)
