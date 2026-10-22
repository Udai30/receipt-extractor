"""What every pipeline returns for one image (BACKEND_SCHEMA §1.2, TRD §4.2).

``ExtractionResult`` is stored as ``ResultRow.prediction``; the scorer reads its ``receipt``.
It carries metadata only — never the image — and the OCR text is excluded from its serialised
form (``ocr_text`` is kept in memory for the caller; eval tasks store it in ``details`` because
CORD is public data, the UI never persists it).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from receipt_extractor.core.receipt import Receipt

__all__ = ["MAX_RAW_TEXT_CHARS", "ExtractionResult", "FailureKind", "StageLatency"]

MAX_RAW_TEXT_CHARS: Final = 8000

FailureKind = Literal["ocr_empty", "schema_invalid", "truncated"]
"""Why ``receipt`` is ``None``: no OCR text to structure, a reply that does not validate as
``Receipt``, or a reply cut off at ``max_tokens``."""


class StageLatency(BaseModel):
    """Wall-clock milliseconds per stage; ``None`` when the stage did not run.

    ``ocr`` is engine time (excluding the wait for the engine lock). ``llm`` is time in the
    provider calls (incl. retry backoff and the repair call); ``llm_wait`` is the time those
    calls were queued for the free-tier budget, rate limiter or a concurrency slot — an
    artefact of pacing, kept apart so it does not pass for model latency. On a cache hit both
    are ~0 (``ExtractionResult.cache_hit`` says so).
    """

    # `validate` would shadow BaseModel.validate, hence the alias (serialised as "validate").
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_by_name=True, serialize_by_alias=True
    )

    ocr: int | None = Field(default=None, ge=0)
    llm: int | None = Field(default=None, ge=0)
    llm_wait: int | None = Field(default=None, ge=0)
    validate_: int | None = Field(default=None, ge=0, alias="validate")


class ExtractionResult(BaseModel):
    """One pipeline's answer for one receipt image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline: str = Field(min_length=1, max_length=64)
    receipt: Receipt | None
    schema_valid: bool
    attempts: int = Field(ge=0, description="LLM calls made: 0 (nothing to send), 1, or 2")
    # Arithmetic reconciliation and the review flag arrive with M2.2; None = not checked.
    reconciled: bool | None = None
    needs_review: bool | None = None
    reconcile_diff: Decimal | None = None
    failure: FailureKind | None = None
    model_alias: str | None = None
    prompt_version: str | None = None
    ocr_text_chars: int | None = Field(default=None, ge=0)
    ocr_lines: int | None = Field(default=None, ge=0)
    ocr_truncated: bool = False
    stage_latency_ms: StageLatency = Field(default_factory=StageLatency)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    list_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    cache_hit: bool = False
    raw_text: str | None = Field(
        default=None,
        max_length=MAX_RAW_TEXT_CHARS,
        description="The model's reply, kept only when it did not validate (failure analysis)",
    )
    ocr_text: str | None = Field(default=None, exclude=True, repr=False)
