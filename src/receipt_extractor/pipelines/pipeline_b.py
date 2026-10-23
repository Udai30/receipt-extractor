"""Pipeline B: receipt image → Gemini Flash-Lite (vision) → ``Receipt`` (PRD FR-4, APPFLOW §2).

**Data policy.** Gemini's free tier may use what it is sent, so B accepts public dataset images
(CORD) only. :meth:`PipelineB.extract` refuses any other ``origin`` *before* the image is even
decoded; the LLM layer refuses it again for the ``public_only`` alias (TRD §S3). The UI never
offers B on an upload (APPFLOW §2).

**Image.** Validated like A's input (size, format and dimensions from the header), downscaled
to ``image.max_side`` (never upscaled) and re-encoded in memory, which also strips metadata.
The size sent and the input tokens the provider bills for the image are recorded.

**Validation.** One structured-output call with ``Receipt`` as the schema; on a schema failure
the errors are sent back up to ``max_repairs`` (default 2) times, then the receipt is checked
arithmetically and flagged ``needs_review`` when invalid or not adding up — the same machinery
as variant ``A`` (TRD §4.3). Nothing here logs or stores image content.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Final

from receipt_extractor.core.extraction import ExtractionResult, StageLatency
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.core.reconcile import REL_TOLERANCE
from receipt_extractor.llm import CallTags, DataPolicyError, LLMClient, LLMRequest
from receipt_extractor.llm.types import DataOrigin
from receipt_extractor.pipelines.images import ModelImageSettings, prepare_model_image
from receipt_extractor.pipelines.pipeline_a import DEFAULT_MAX_REPAIRS
from receipt_extractor.pipelines.prompts import (
    DEFAULT_VISION_PROMPT_VERSION,
    VISION_PROMPTS,
    build_vision_messages,
)
from receipt_extractor.pipelines.replies import parse_receipt_reply, review

__all__ = ["ALLOWED_ORIGIN", "DEFAULT_MAX_TOKENS", "DEFAULT_MODEL_ALIAS", "PipelineB"]

DEFAULT_MODEL_ALIAS: Final = "gemini-flash-lite"
# Gemini 3.x Flash models may think before answering; thinking tokens count against max_tokens.
DEFAULT_MAX_TOKENS: Final = 8192
ALLOWED_ORIGIN: Final[DataOrigin] = "public_dataset"
VARIANT: Final = "B"


class PipelineB:
    def __init__(
        self,
        *,
        client: LLMClient,
        model_alias: str = DEFAULT_MODEL_ALIAS,
        prompt_version: str = DEFAULT_VISION_PROMPT_VERSION,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        image: ModelImageSettings | None = None,
        seed: int | None = None,
        variant: str = VARIANT,
        timeout_s: float | None = None,
        max_repairs: int = DEFAULT_MAX_REPAIRS,
        reconcile_tolerance: Decimal = REL_TOLERANCE,
    ) -> None:
        if prompt_version not in VISION_PROMPTS:
            raise ValueError(f"unknown vision prompt version {prompt_version!r}")
        self._client = client
        self.model_alias = model_alias
        self.prompt_version = prompt_version
        self.max_tokens = max_tokens
        self.image_settings = image or ModelImageSettings()
        self.seed = seed
        self.variant = variant
        # A local CPU model can need minutes per receipt; None = the client's default timeout.
        self.timeout_s = timeout_s
        self.max_repairs = max_repairs
        self.reconcile_tolerance = reconcile_tolerance

    @property
    def name(self) -> str:
        return self.variant

    async def extract(
        self, image: bytes, *, origin: DataOrigin, tags: CallTags | None = None
    ) -> ExtractionResult:
        if origin != ALLOWED_ORIGIN:
            # Checked first: a refused image is never decoded, resized or sent anywhere.
            raise DataPolicyError(
                f"pipeline {self.variant} accepts public dataset images only (origin is '{origin}')"
            )
        tags = tags or CallTags()

        start = time.perf_counter()
        prepared = await asyncio.to_thread(prepare_model_image, image, self.image_settings)
        prepare_ms = max(0, round((time.perf_counter() - start) * 1000))

        response = await self._client.complete(
            LLMRequest(
                model_alias=self.model_alias,
                messages=build_vision_messages(self.prompt_version, prepared),
                temperature=0.0,
                max_tokens=self.max_tokens,
                seed=self.seed,
                response_model=Receipt,
                repair=True,
                max_repairs=self.max_repairs,
                data_origin=origin,
                tags=tags.model_copy(update={"stage": "llm"}),
                timeout_s=self.timeout_s,
            )
        )
        parsed = parse_receipt_reply(response)
        checked = review(parsed.receipt, rel_tolerance=self.reconcile_tolerance)
        return ExtractionResult(
            pipeline=self.variant,
            receipt=parsed.receipt,
            schema_valid=parsed.receipt is not None,
            attempts=response.attempts,
            reconciled=checked.reconciled,
            needs_review=checked.needs_review,
            reconcile_diff=checked.reconcile_diff,
            failure=parsed.failure,
            model_alias=self.model_alias,
            prompt_version=self.prompt_version,
            image_width=prepared.width,
            image_height=prepared.height,
            image_tokens_in=response.image_tokens_in,
            stage_latency_ms=StageLatency(
                prepare=prepare_ms,
                llm=response.latency_ms,
                llm_wait=response.wait_ms,
                validate_=parsed.validate_ms,
            ),
            tokens_in=response.tokens_in or 0,
            tokens_out=response.tokens_out or 0,
            list_cost_usd=response.list_cost_usd or Decimal(0),
            cache_hit=response.cache_hit,
            raw_text=parsed.raw_text,
        )
