"""The ``pipeline_a`` eval task: runs :class:`PipelineA` on CORD cases (TRD §S4).

Params (validated, part of the run id): ``mode`` (``naive`` | ``engineered``), ``model`` (an
alias from ``configs/models.yaml``), ``prompt_version``, ``max_tokens`` and ``ocr`` settings.
The LLM client is built on the first case with the disk cache ON (TRD §S3: eval runs re-run for
free) and the run's ``llm_calls.jsonl`` as its sink; the OCR engine is built on first use too.

Case images are public CORD data (``data_origin = public_dataset``). The image path comes from
the dataset manifest; it is confined to ``data/`` and its SHA-256 checked before use.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from receipt_extractor.eval.datasets import Case, describe_validation_error
from receipt_extractor.eval.errors import DatasetError, RunConfigError
from receipt_extractor.eval.paths import DATA_DIR, confine
from receipt_extractor.eval.tasks import CaseContext, CaseOutput, register_task
from receipt_extractor.llm import CallTags, JSONLSink, LLMClient
from receipt_extractor.pipelines.base import Pipeline
from receipt_extractor.pipelines.ocr import MAX_IMAGE_BYTES, OcrSettings, OcrStage, RapidOcrEngine
from receipt_extractor.pipelines.pipeline_a import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_ALIAS,
    Mode,
    PipelineA,
)
from receipt_extractor.pipelines.prompts import DEFAULT_PROMPT_VERSION, PROMPTS

__all__ = ["PipelineAParams", "PipelineATask"]

ClientFactory = Callable[[CaseContext], LLMClient]
OcrFactory = Callable[[OcrSettings], OcrStage]


class PipelineAParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Mode
    model: str = Field(default=DEFAULT_MODEL_ALIAS, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    prompt_version: str = DEFAULT_PROMPT_VERSION
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, ge=256, le=65536)
    ocr: OcrSettings = Field(default_factory=OcrSettings)

    @field_validator("prompt_version")
    @classmethod
    def _known_prompt(cls, value: str) -> str:
        if value not in PROMPTS:
            raise ValueError(f"unknown prompt version (known: {', '.join(sorted(PROMPTS))})")
        return value


def _default_client(ctx: CaseContext) -> LLMClient:
    from receipt_extractor.config import get_settings  # noqa: PLC0415
    from receipt_extractor.llm import build_client  # noqa: PLC0415

    return build_client(get_settings(), cache=True, sinks=[JSONLSink(ctx.llm_calls_path)])


def _default_ocr(settings: OcrSettings) -> OcrStage:
    return OcrStage(RapidOcrEngine(settings))


class PipelineATask:
    def __init__(
        self,
        params: PipelineAParams,
        *,
        client_factory: ClientFactory = _default_client,
        ocr_factory: OcrFactory = _default_ocr,
    ) -> None:
        self.params = params
        self._client_factory = client_factory
        self._ocr_factory = ocr_factory
        self._pipeline: Pipeline | None = None
        self._lock = asyncio.Lock()

    async def _get_pipeline(self, ctx: CaseContext) -> Pipeline:
        async with self._lock:
            if self._pipeline is None:
                params = self.params
                self._pipeline = PipelineA(
                    client=self._client_factory(ctx),
                    ocr=self._ocr_factory(params.ocr),
                    mode=params.mode,
                    model_alias=params.model,
                    prompt_version=params.prompt_version,
                    max_tokens=params.max_tokens,
                    seed=ctx.seed,
                )
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
            details={"ocr_text": result.ocr_text},
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            list_cost_usd=result.list_cost_usd,
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


def _factory(params: Mapping[str, Any]) -> PipelineATask:
    try:
        return PipelineATask(PipelineAParams.model_validate(dict(params)))
    except ValidationError as exc:
        raise RunConfigError(
            describe_validation_error(exc, "params of task 'pipeline_a'")
        ) from None


register_task("pipeline_a", _factory)
