"""Turning a structured-output reply into a ``Receipt`` (or a failure), and reviewing it.

Shared by pipelines A and B (TRD §4.3): :func:`parse_receipt_reply` validates the reply (the
LLM layer has already re-asked up to ``max_repairs`` times); :func:`review` runs the arithmetic
check and sets the review flag. ``A-naive`` skips :func:`review` — its flags stay ``None``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from receipt_extractor.core.extraction import (
    MAX_RAW_TEXT_CHARS,
    ExtractionResult,
    FailureKind,
    StageLatency,
)
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.core.reconcile import REL_TOLERANCE, reconcile
from receipt_extractor.llm import LLMResponse
from receipt_extractor.llm.types import extract_json_text

__all__ = [
    "ParsedReply",
    "Review",
    "extraction_from_reply",
    "parse_receipt_reply",
    "parse_receipt_text",
    "review",
]


@dataclass(frozen=True, slots=True)
class ParsedReply:
    receipt: Receipt | None
    failure: FailureKind | None
    validate_ms: int
    raw_text: str | None  # the reply, kept (capped) only when it did not validate


def parse_receipt_reply(response: LLMResponse) -> ParsedReply:
    return parse_receipt_text(response.text, finish_reason=response.finish_reason)


def parse_receipt_text(text: str, *, finish_reason: str | None = None) -> ParsedReply:
    """Validate a model's reply text as ``Receipt`` (a surrounding code fence is tolerated)."""
    start = time.perf_counter()
    receipt: Receipt | None
    try:
        receipt = Receipt.model_validate_json(extract_json_text(text))
    except ValueError:  # ValidationError, incl. malformed JSON
        receipt = None
    validate_ms = max(0, round((time.perf_counter() - start) * 1000))
    if receipt is not None:
        return ParsedReply(receipt=receipt, failure=None, validate_ms=validate_ms, raw_text=None)
    failure: FailureKind = "truncated" if finish_reason == "length" else "schema_invalid"
    return ParsedReply(
        receipt=None,
        failure=failure,
        validate_ms=validate_ms,
        raw_text=text[:MAX_RAW_TEXT_CHARS],
    )


def extraction_from_reply(
    text: str,
    *,
    variant: str,
    model_alias: str | None,
    prompt_version: str | None,
    finish_reason: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    llm_ms: int | None = None,
) -> ExtractionResult:
    """An ``ExtractionResult`` for a reply produced offline (a Kaggle notebook, M2.4).

    The same validation and review as pipelines A and B, so imported rows are scored and
    flagged exactly like local ones. One call, no repairs: the notebook has no re-ask loop.
    """
    parsed = parse_receipt_text(text, finish_reason=finish_reason)
    checked = review(parsed.receipt)
    return ExtractionResult(
        pipeline=variant,
        receipt=parsed.receipt,
        schema_valid=parsed.receipt is not None,
        attempts=1,
        reconciled=checked.reconciled,
        needs_review=checked.needs_review,
        reconcile_diff=checked.reconcile_diff,
        failure=parsed.failure,
        model_alias=model_alias,
        prompt_version=prompt_version,
        stage_latency_ms=StageLatency(llm=llm_ms, validate_=parsed.validate_ms),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        list_cost_usd=Decimal(0),
        raw_text=parsed.raw_text,
    )


@dataclass(frozen=True, slots=True)
class Review:
    """``ExtractionResult``'s review fields (BACKEND_SCHEMA §1.2, APPFLOW §3 states)."""

    reconciled: bool | None  # None: nothing to check (no valid receipt)
    reconcile_diff: Decimal | None  # signed difference of the failing (or the totals) check
    needs_review: bool


def review(receipt: Receipt | None, *, rel_tolerance: Decimal = REL_TOLERANCE) -> Review:
    """Arithmetic check of a validated receipt; anything invalid or not adding up needs review.

    ``reconcile_diff`` is the item-sum difference when that check fails, otherwise the totals
    difference (``(subtotal or Σ items) + tax - total``), so the UI can show what is off.
    """
    if receipt is None:
        return Review(reconciled=None, reconcile_diff=None, needs_review=True)
    check = reconcile(receipt, rel_tolerance=rel_tolerance)
    if check.ok:
        diff = Decimal(0) if check.tax_included else check.total_diff
    elif check.items_diff is not None and abs(check.items_diff) > check.allowed:
        diff = check.items_diff
    else:
        diff = check.total_diff
    return Review(reconciled=check.ok, reconcile_diff=diff, needs_review=not check.ok)
