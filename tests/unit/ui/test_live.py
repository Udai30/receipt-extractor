"""Live uploads: input caps, demo budget, error handling, and "never sent to Gemini"."""

from __future__ import annotations

import io
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from receipt_extractor.core.extraction import ExtractionResult
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.llm.errors import (
    BudgetExhaustedError,
    MissingCredentialsError,
    RateLimitedError,
)
from receipt_extractor.llm.types import CallTags, DataOrigin
from receipt_extractor.pipelines.images import ImageRejectedError
from receipt_extractor.ui.app import consume_upload
from receipt_extractor.ui.live import (
    MAX_UPLOAD_BYTES,
    PAUSED_MESSAGE,
    DemoBudget,
    LiveService,
    check_upload,
)
from receipt_extractor.ui.presenters import B_UPLOAD_NOTE, present_live, present_sample
from receipt_extractor.ui.showcase import SHOWCASE_DIR, load_showcase


def png(width: int = 40, height: int = 60) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


OK_RESULT = ExtractionResult(
    pipeline="A",
    receipt=Receipt.model_validate({"items": [], "total": "1.000"}),
    schema_valid=True,
    attempts=1,
    reconciled=True,
    needs_review=False,
    reconcile_diff=Decimal(0),
    ocr_text="TOTAL 1.000",
)


class FakePipeline:
    name = "A"

    def __init__(self, outcome: ExtractionResult | BaseException = OK_RESULT) -> None:
        self.outcome = outcome
        self.calls: list[DataOrigin] = []

    async def extract(
        self, image: bytes, *, origin: DataOrigin, tags: CallTags | None = None
    ) -> ExtractionResult:
        self.calls.append(origin)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def service(pipeline: FakePipeline, per_day: int | None = None) -> LiveService:
    return LiveService(lambda: pipeline, budget=DemoBudget(per_day))


# --- input caps ------------------------------------------------------------------------------


def test_valid_upload_passes() -> None:
    check_upload(png())


BAD_UPLOADS = {
    "empty": (lambda: b"", "empty"),
    "over-5-mb": (lambda: b"\x89PNG\r\n\x1a\n" + b"\0" * MAX_UPLOAD_BYTES, "larger than 5 MB"),
    "gif": (lambda: b"GIF89a" + b"\0" * 40, "JPEG, PNG or WebP"),
    "corrupt-png": (lambda: b"\x89PNG\r\n\x1a\n" + b"garbage", "could not be read"),
}


@pytest.mark.parametrize("case", sorted(BAD_UPLOADS))
def test_bad_uploads_are_rejected(case: str) -> None:
    make, message = BAD_UPLOADS[case]
    with pytest.raises(ImageRejectedError, match=message):
        check_upload(make())


def test_too_many_pixels_rejected_from_the_header() -> None:
    with pytest.raises(ImageRejectedError, match="4096 px per side"):
        check_upload(png(4100, 10))


# --- budget ----------------------------------------------------------------------------------


def test_demo_budget_caps_per_day_and_resets() -> None:
    day = [date(2026, 10, 23)]
    budget = DemoBudget(2, today=lambda: day[0])
    assert budget.try_take() and budget.try_take()
    assert not budget.try_take() and budget.paused
    day[0] = date(2026, 10, 24)
    assert not budget.paused and budget.try_take()


def test_no_cap_outside_demo_mode_until_the_provider_says_stop() -> None:
    budget = DemoBudget(None)
    assert all(budget.try_take() for _ in range(500)) and not budget.paused
    budget.exhaust()
    assert budget.paused and not budget.try_take()


# --- the service -----------------------------------------------------------------------------


async def test_upload_runs_pipeline_a_as_user_upload() -> None:
    pipeline = FakePipeline()
    outcome = await service(pipeline).extract(png())
    assert outcome.kind == "ok" and outcome.result == OK_RESULT
    assert pipeline.calls == ["user_upload"]  # the layer refuses public_only aliases for this
    assert len(outcome.request_id) == 8


async def test_rejected_upload_never_reaches_the_pipeline() -> None:
    pipeline = FakePipeline()
    outcome = await service(pipeline).extract(b"not an image")
    assert outcome.kind == "rejected" and pipeline.calls == []


async def test_exhausted_demo_budget_pauses_without_calling() -> None:
    pipeline = FakePipeline()
    outcome = await service(pipeline, per_day=0).extract(png())
    assert outcome.kind == "paused" and outcome.message == PAUSED_MESSAGE
    assert pipeline.calls == []


@pytest.mark.parametrize(
    "error",
    [
        BudgetExhaustedError("groq-oss-20b", "token", 200_000, 200_000),
        RateLimitedError("429 Too Many Requests", status_code=429),
    ],
)
async def test_provider_limits_pause_live_calls(error: BaseException) -> None:
    live = service(FakePipeline(error))
    outcome = await live.extract(png())
    assert outcome.kind == "paused"
    if isinstance(error, BudgetExhaustedError):
        assert live.budget.paused  # no further calls today


async def test_missing_key_is_explained_without_details() -> None:
    outcome = await service(
        FakePipeline(MissingCredentialsError("GROQ_API_KEY is not set"))
    ).extract(png())
    assert outcome.kind == "error" and "not configured" in outcome.message
    assert "GROQ_API_KEY" not in outcome.message


async def test_unexpected_errors_are_generic_with_a_request_id(caplog: Any) -> None:
    detail = "Traceback: provider said token-123 at /home/x.py line 3"
    outcome = await service(FakePipeline(RuntimeError(detail))).extract(png())
    assert outcome.kind == "error" and outcome.request_id in outcome.message
    assert detail not in outcome.message
    assert detail not in caplog.text  # logs carry the type only


def test_default_service_builds_pipeline_a_only(clean_env: pytest.MonkeyPatch) -> None:
    from receipt_extractor.config import get_settings  # noqa: PLC0415
    from receipt_extractor.pipelines.pipeline_a import PipelineA  # noqa: PLC0415
    from receipt_extractor.ui.live import default_live_service  # noqa: PLC0415

    root = Path(__file__).resolve().parents[3]
    clean_env.chdir(root)
    clean_env.setenv("DEMO_MODE", "true")
    clean_env.setenv("DEMO_DAILY_REQUESTS", "7")
    get_settings.cache_clear()
    try:
        live = default_live_service()
        pipeline = live._get()
        assert isinstance(pipeline, PipelineA)
        assert pipeline.mode == "engineered" and pipeline.model_alias == "groq-oss-20b"
        assert live.budget.per_day == 7
    finally:
        get_settings.cache_clear()


# --- presenters and the upload temp file -------------------------------------------------------


def test_live_presentation_never_shows_b() -> None:
    from receipt_extractor.ui.live import LiveOutcome  # noqa: PLC0415

    ok = present_live(LiveOutcome("ok", "abcd1234", result=OK_RESULT))
    assert "abcd1234" in ok.banner and "Totals add up" in ok.a[1]
    assert B_UPLOAD_NOTE in ok.b[1] and ok.b[2] == []
    paused = present_live(LiveOutcome("paused", "x", message=PAUSED_MESSAGE))
    assert paused.banner.startswith("⏸") and B_UPLOAD_NOTE in paused.b[1]


def test_bundled_samples_show_cached_a_and_b() -> None:
    showcase = load_showcase()
    assert showcase is not None and len(showcase.cases) == 6
    assert all(c.case_id.startswith("validation-") for c in showcase.cases)
    assert all((SHOWCASE_DIR / c.image).is_file() for c in showcase.cases)
    out = present_sample(showcase, showcase.cases[0].case_id)
    assert "cached results from runs" in out.banner and out.image is not None
    assert out.a[0].startswith("### A") and out.b[0].startswith("### B")
    assert out.a[2] and out.b[2]  # both have line items
    assert present_sample(showcase, None).image is None


def test_upload_temp_file_is_read_then_deleted() -> None:
    path = Path(tempfile.mkdtemp()) / "upload.png"
    path.write_bytes(png())
    assert consume_upload(path) == png()
    assert not path.exists()


def test_upload_outside_temp_dirs_is_refused(tmp_path: Path) -> None:
    outside = Path(__file__)
    with pytest.raises(ValueError, match="unexpected upload location"):
        consume_upload(outside)
    assert outside.exists()


def test_oversized_temp_file_is_not_read_into_memory() -> None:
    path = Path(tempfile.mkdtemp()) / "big.png"
    with path.open("wb") as handle:
        handle.truncate(MAX_UPLOAD_BYTES + 10)
    data = consume_upload(path)
    assert len(data) == MAX_UPLOAD_BYTES + 1 and not path.exists()
    with pytest.raises(ImageRejectedError, match="larger than 5 MB"):
        check_upload(data)


def test_requirements_keep_pillow_13_and_the_space_card_is_static() -> None:
    root = Path(__file__).resolve().parents[3]
    lines = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = {
        line.split("==")[0].lower(): line
        for line in lines
        if "==" in line and not line.startswith("#")
    }
    assert pins["pillow"].startswith("pillow==13.")
    assert "gradio" not in pins and "gradio-client" not in pins  # installed from sdk_version
    # The published Space is static (free); the Gradio app runs locally (WORKLOG 2026-10-28).
    front = (root / "space" / "README.md").read_text(encoding="utf-8").split("---")[1]
    assert "sdk: static" in front and "app_file: index.html" in front
