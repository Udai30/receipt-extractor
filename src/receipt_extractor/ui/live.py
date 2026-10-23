"""Live extraction for uploads in the demo: pipeline A only, guarded (TRD §S7, APPFLOW J2).

- **Uploads never reach Gemini.** This service can only build pipeline A (RapidOCR + Groq) and
  always passes ``origin="user_upload"``, which the LLM layer refuses for every ``public_only``
  alias as a second guard. There is no code path from here to pipeline B.
- **Input caps, server-side** (not only in the component): ≤ 5 MB, PNG/JPEG/WebP magic, ≤ 4,096 px
  per side, all checked from the bytes and header before anything is decoded or sent.
- **Budget:** with ``DEMO_MODE`` on, live calls stop after ``DEMO_DAILY_REQUESTS`` per UTC day;
  provider rate limits and the daily token budget end the same way. Callers get a ``paused``
  outcome and the UI shows its banner; samples keep working from the cached bundle.
- **Privacy:** bytes stay in memory; logs carry metadata only (request id, size, ms, outcome) —
  never image content, OCR text or model output. Live traffic does not use the disk cache.
- **Errors:** plain language + a short request id; no stack traces or provider messages.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final, Literal

from receipt_extractor.core.extraction import ExtractionResult
from receipt_extractor.llm.errors import (
    BudgetExhaustedError,
    MissingCredentialsError,
    RateLimitedError,
    RetriesExhaustedError,
)
from receipt_extractor.pipelines.base import Pipeline
from receipt_extractor.pipelines.images import ImageRejectedError

__all__ = [
    "MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_SIDE",
    "DemoBudget",
    "LiveOutcome",
    "LiveService",
    "check_upload",
]

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES: Final = 5 * 1024 * 1024
MAX_UPLOAD_SIDE: Final = 4096
PAUSED_MESSAGE: Final = (
    "Live calls are paused for today (free-tier budget reached). "
    "The sample receipts still show cached results."
)

OutcomeKind = Literal["ok", "rejected", "paused", "error"]


@dataclass(frozen=True, slots=True)
class LiveOutcome:
    kind: OutcomeKind
    request_id: str
    result: ExtractionResult | None = None
    message: str = ""


def check_upload(data: bytes) -> None:
    """Demo input caps, from bytes and header only (raises ``ImageRejectedError``)."""
    from PIL import Image  # noqa: PLC0415

    if not data:
        raise ImageRejectedError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejectedError("The image is larger than 5 MB. Please upload a smaller photo.")
    formats = {b"\x89PNG\r\n\x1a\n": "PNG", b"\xff\xd8\xff": "JPEG"}
    kind = next((name for magic, name in formats.items() if data.startswith(magic)), None)
    if kind is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = "WEBP"
    if kind is None:
        raise ImageRejectedError("Please upload a JPEG, PNG or WebP image.")
    try:
        with Image.open(io.BytesIO(data), formats=[kind]) as image:
            width, height = image.size  # header only
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageRejectedError("The image could not be read.") from None
    if max(width, height) > MAX_UPLOAD_SIDE:
        raise ImageRejectedError(
            f"The image is {width} x {height} px; "
            f"please keep it under {MAX_UPLOAD_SIDE} px per side."
        )


class DemoBudget:
    """Live calls allowed per UTC day (``None`` = no demo cap). Thread-safe."""

    def __init__(self, per_day: int | None, *, today: Callable[[], date] | None = None) -> None:
        self.per_day = per_day
        self._today = today or (lambda: datetime.now(UTC).date())
        self._day = self._today()
        self._used = 0
        self._stopped = False  # the provider said "no more today"
        self._lock = threading.Lock()

    def _roll(self) -> None:  # caller holds the lock
        day = self._today()
        if day != self._day:
            self._day, self._used, self._stopped = day, 0, False

    def _blocked(self) -> bool:  # caller holds the lock
        capped = self.per_day is not None and self._used >= self.per_day
        return self._stopped or capped

    def try_take(self) -> bool:
        with self._lock:
            self._roll()
            if self._blocked():
                return False
            self._used += 1
            return True

    def exhaust(self) -> None:
        """Provider said "no more today": stop live calls until the next UTC day."""
        with self._lock:
            self._roll()
            self._stopped = True

    @property
    def paused(self) -> bool:
        with self._lock:
            self._roll()
            return self._blocked()


class LiveService:
    """Runs pipeline A on an uploaded image. ``pipeline_factory`` is called once, lazily."""

    def __init__(self, pipeline_factory: Callable[[], Pipeline], *, budget: DemoBudget) -> None:
        self._factory = pipeline_factory
        self._pipeline: Pipeline | None = None
        self._lock = threading.Lock()
        self.budget = budget

    def _get(self) -> Pipeline:
        with self._lock:
            if self._pipeline is None:
                self._pipeline = self._factory()
            return self._pipeline

    async def extract(self, data: bytes) -> LiveOutcome:
        """Async, so every call shares the event loop the LLM client's limiters live on."""
        request_id = uuid.uuid4().hex[:8]
        try:
            check_upload(data)
        except ImageRejectedError as exc:
            logger.info("upload %s rejected (%d bytes)", request_id, len(data))
            return LiveOutcome("rejected", request_id, message=str(exc))
        if not self.budget.try_take():
            return LiveOutcome("paused", request_id, message=PAUSED_MESSAGE)
        return await self._run(data, request_id)

    async def _run(self, data: bytes, request_id: str) -> LiveOutcome:
        start = time.perf_counter()
        try:
            result = await self._get().extract(data, origin="user_upload")
        except ImageRejectedError:
            return LiveOutcome("rejected", request_id, message="The image could not be read.")
        except (BudgetExhaustedError, RateLimitedError, RetriesExhaustedError) as exc:
            if isinstance(exc, BudgetExhaustedError):
                self.budget.exhaust()
            logger.warning("upload %s paused: %s", request_id, type(exc).__name__)
            return LiveOutcome("paused", request_id, message=PAUSED_MESSAGE)
        except MissingCredentialsError:
            logger.error("upload %s: live extraction is not configured", request_id)
            return LiveOutcome(
                "error",
                request_id,
                message="Live extraction is not configured on this deployment. "
                "Try a sample receipt instead.",
            )
        except Exception as exc:
            logger.error("upload %s failed: %s", request_id, type(exc).__name__)
            return LiveOutcome(
                "error",
                request_id,
                message="Something went wrong while reading this receipt. "
                f"Please try again or use a sample receipt (request id {request_id}).",
            )
        logger.info(
            "upload %s done in %d ms (%d bytes, valid=%s)",
            request_id,
            round((time.perf_counter() - start) * 1000),
            len(data),
            result.schema_valid,
        )
        return LiveOutcome("ok", request_id, result=result)


def default_live_service() -> LiveService:
    """Production wiring: engineered pipeline A, no disk cache (live traffic, TRD §S3)."""
    from receipt_extractor.config import get_settings  # noqa: PLC0415
    from receipt_extractor.llm import build_client  # noqa: PLC0415
    from receipt_extractor.pipelines.ocr import OcrStage, RapidOcrEngine  # noqa: PLC0415
    from receipt_extractor.pipelines.pipeline_a import PipelineA  # noqa: PLC0415

    settings = get_settings()

    def factory() -> Pipeline:
        return PipelineA(
            client=build_client(settings, cache=False),
            ocr=OcrStage(RapidOcrEngine()),
            mode="engineered",
        )

    per_day = settings.demo_daily_requests if settings.demo_mode else None
    return LiveService(factory, budget=DemoBudget(per_day))
