"""Pipeline A: RapidOCR text → Groq ``gpt-oss-20b`` → ``Receipt`` (PRD FR-3, APPFLOW §2).

Two modes, same prompt and model, so the difference is the validation machinery alone:

- ``naive`` (variant ``A-naive``, the baseline): exactly one structured-output call; a reply
  that does not validate is the answer (``receipt = None``). No repair, no checks.
- ``engineered`` (variant ``A``): the LLM layer's repair — on a schema failure the validation
  errors are sent back once (TRD §S3). M2.2 adds a second retry, arithmetic reconciliation
  and ``needs_review`` here.

The image is processed in memory; nothing here logs or stores image or text content.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Final, Literal

from receipt_extractor.core.extraction import (
    MAX_RAW_TEXT_CHARS,
    ExtractionResult,
    FailureKind,
    StageLatency,
)
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.llm import CallTags, LLMClient, LLMRequest
from receipt_extractor.llm.types import DataOrigin
from receipt_extractor.pipelines.ocr import OcrResult, OcrStage
from receipt_extractor.pipelines.prompts import DEFAULT_PROMPT_VERSION, PROMPTS, build_messages

__all__ = ["DEFAULT_MAX_TOKENS", "DEFAULT_MODEL_ALIAS", "Mode", "PipelineA"]

Mode = Literal["naive", "engineered"]

DEFAULT_MODEL_ALIAS: Final = "groq-oss-20b"
# gpt-oss is a reasoning model: max_tokens covers its reasoning as well as the JSON.
DEFAULT_MAX_TOKENS: Final = 4096
_VARIANT: Final[dict[Mode, str]] = {"naive": "A-naive", "engineered": "A"}


def _elapsed_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


class PipelineA:
    def __init__(
        self,
        *,
        client: LLMClient,
        ocr: OcrStage,
        mode: Mode,
        model_alias: str = DEFAULT_MODEL_ALIAS,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        seed: int | None = None,
    ) -> None:
        if prompt_version not in PROMPTS:
            raise ValueError(f"unknown prompt version {prompt_version!r}")
        self._client = client
        self._ocr = ocr
        self.mode: Mode = mode
        self.model_alias = model_alias
        self.prompt_version = prompt_version
        self.max_tokens = max_tokens
        self.seed = seed

    @property
    def name(self) -> str:
        return _VARIANT[self.mode]

    async def extract(
        self, image: bytes, *, origin: DataOrigin, tags: CallTags | None = None
    ) -> ExtractionResult:
        tags = tags or CallTags()
        ocr = await asyncio.to_thread(self._ocr.run, image, case_id=tags.case_id)
        if not ocr.text.strip():
            # Nothing to structure: a model call could only invent a receipt.
            return self._result(
                ocr,
                receipt=None,
                attempts=0,
                failure="ocr_empty",
                stage_latency_ms=StageLatency(ocr=ocr.latency_ms),
            )

        messages, truncated = build_messages(self.prompt_version, ocr.text)
        response = await self._client.complete(
            LLMRequest(
                model_alias=self.model_alias,
                messages=messages,
                temperature=0.0,
                max_tokens=self.max_tokens,
                seed=self.seed,
                response_model=Receipt,
                repair=self.mode == "engineered",
                data_origin=origin,
                tags=tags.model_copy(update={"stage": "llm"}),
            )
        )

        start = time.perf_counter()
        receipt: Receipt | None
        try:
            receipt = response.parse(Receipt)
        except ValueError:
            receipt = None
        validate_ms = _elapsed_ms(start)

        failure: FailureKind | None = None
        if receipt is None:
            failure = "truncated" if response.finish_reason == "length" else "schema_invalid"
        return self._result(
            ocr,
            receipt=receipt,
            attempts=2 if response.repaired else 1,
            failure=failure,
            ocr_truncated=truncated,
            stage_latency_ms=StageLatency(
                ocr=ocr.latency_ms,
                llm=response.latency_ms,
                llm_wait=response.wait_ms,
                validate_=validate_ms,
            ),
            tokens_in=response.tokens_in or 0,
            tokens_out=response.tokens_out or 0,
            list_cost_usd=response.list_cost_usd or Decimal(0),
            cache_hit=response.cache_hit,
            raw_text=None if receipt is not None else response.text[:MAX_RAW_TEXT_CHARS],
        )

    def _result(
        self,
        ocr: OcrResult,
        *,
        receipt: Receipt | None,
        attempts: int,
        failure: FailureKind | None,
        stage_latency_ms: StageLatency,
        ocr_truncated: bool = False,
        tokens_in: int = 0,
        tokens_out: int = 0,
        list_cost_usd: Decimal = Decimal(0),
        cache_hit: bool = False,
        raw_text: str | None = None,
    ) -> ExtractionResult:
        return ExtractionResult(
            pipeline=self.name,
            receipt=receipt,
            schema_valid=receipt is not None,
            attempts=attempts,
            failure=failure,
            model_alias=self.model_alias,
            prompt_version=self.prompt_version,
            ocr_text_chars=len(ocr.text),
            ocr_lines=ocr.lines,
            ocr_truncated=ocr_truncated,
            stage_latency_ms=stage_latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            list_cost_usd=list_cost_usd,
            cache_hit=cache_hit,
            raw_text=raw_text,
            ocr_text=ocr.text,
        )
