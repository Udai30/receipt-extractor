"""Pipeline A: RapidOCR text → Groq ``gpt-oss-20b`` → ``Receipt`` (PRD FR-3, APPFLOW §2).

Two modes, same prompt and model, so the difference is the validation machinery alone:

- ``naive`` (variant ``A-naive``, the baseline): exactly one structured-output call; a reply
  that does not validate is the answer (``receipt = None``). No repair, no checks.
- ``engineered`` (variant ``A``): on a schema failure the validation errors are sent back up
  to ``max_repairs`` (default 2) times (TRD §4.3, §S3); a valid receipt is then checked
  arithmetically (``reconciled``, ``reconcile_diff``) and anything invalid or not adding up is
  flagged ``needs_review`` (M2.2). Naive leaves all three ``None``.

The image is processed in memory; nothing here logs or stores image or text content.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Final, Literal

from receipt_extractor.core.extraction import ExtractionResult, FailureKind, StageLatency
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.core.reconcile import REL_TOLERANCE
from receipt_extractor.llm import CallTags, LLMClient, LLMRequest
from receipt_extractor.llm.types import DataOrigin
from receipt_extractor.pipelines.ocr import OcrResult, OcrStage
from receipt_extractor.pipelines.prompts import DEFAULT_PROMPT_VERSION, PROMPTS, build_messages
from receipt_extractor.pipelines.replies import Review, parse_receipt_reply, review

__all__ = [
    "DEFAULT_MAX_REPAIRS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL_ALIAS",
    "Mode",
    "PipelineA",
]

Mode = Literal["naive", "engineered"]

DEFAULT_MODEL_ALIAS: Final = "groq-oss-20b"
# gpt-oss is a reasoning model: max_tokens covers its reasoning as well as the JSON.
DEFAULT_MAX_TOKENS: Final = 4096
DEFAULT_MAX_REPAIRS: Final = 2  # FR-5 / TRD §4.3: re-ask with the errors at most twice
_VARIANT: Final[dict[Mode, str]] = {"naive": "A-naive", "engineered": "A"}


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
        max_repairs: int = DEFAULT_MAX_REPAIRS,
        reconcile_tolerance: Decimal = REL_TOLERANCE,
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
        self.max_repairs = max_repairs
        self.reconcile_tolerance = reconcile_tolerance

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
                checked=self._review(None),
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
                max_repairs=self.max_repairs,
                data_origin=origin,
                tags=tags.model_copy(update={"stage": "llm"}),
            )
        )

        parsed = parse_receipt_reply(response)
        return self._result(
            ocr,
            receipt=parsed.receipt,
            attempts=response.attempts,
            failure=parsed.failure,
            checked=self._review(parsed.receipt),
            ocr_truncated=truncated,
            stage_latency_ms=StageLatency(
                ocr=ocr.latency_ms,
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

    def _review(self, receipt: Receipt | None) -> Review | None:
        if self.mode == "naive":
            return None  # the baseline has no checks
        return review(receipt, rel_tolerance=self.reconcile_tolerance)

    def _result(
        self,
        ocr: OcrResult,
        *,
        receipt: Receipt | None,
        attempts: int,
        failure: FailureKind | None,
        checked: Review | None,
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
            reconciled=checked.reconciled if checked else None,
            needs_review=checked.needs_review if checked else None,
            reconcile_diff=checked.reconcile_diff if checked else None,
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
