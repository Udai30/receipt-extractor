"""Pipeline B against FakeProvider: image → vision model → Receipt, and the data-policy guard."""

from __future__ import annotations

import base64
import io
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from pydantic_core import to_jsonable_python

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.llm import LLMRequest
from receipt_extractor.llm.client import LLMClient
from receipt_extractor.llm.errors import DataPolicyError
from receipt_extractor.llm.fake import FakeProvider, FakeReply
from receipt_extractor.llm.registry import ModelRegistry
from receipt_extractor.llm.sinks import MemorySink
from receipt_extractor.llm.types import CallTags, DataOrigin
from receipt_extractor.pipelines import pipeline_b
from receipt_extractor.pipelines.images import ModelImageSettings
from receipt_extractor.pipelines.pipeline_b import PipelineB
from receipt_extractor.pipelines.prompts import VISION_PROMPTS, build_vision_messages
from tests.unit.pipelines.conftest import png_bytes
from tests.unit.pipelines.test_pipeline_a import INVALID, VALID

Factory = Callable[..., LLMClient]


def pipeline(
    make_client: Factory,
    provider: FakeProvider,
    *,
    cache: bool = False,
    alias: str = "test-public",
    **kwargs: object,
) -> PipelineB:
    return PipelineB(
        client=make_client(provider, cache=cache),
        model_alias=alias,
        seed=0,
        **kwargs,  # type: ignore[arg-type]
    )


def _sent_image(provider: FakeProvider, call: int = 0) -> Image.Image:
    user = provider.calls[call].messages[1]
    (image_part,) = [p for p in user["content"] if p["type"] == "image_url"]
    url: str = image_part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    image.load()
    return image


# --- happy path --------------------------------------------------------------------------


async def test_valid_reply_gives_receipt_and_records_image_metadata(
    make_client: Factory, sink: MemorySink
) -> None:
    provider = FakeProvider(
        [FakeReply(text=VALID, tokens_in=1400, tokens_out=300, image_tokens_in=1120)]
    )
    result = await pipeline(make_client, provider).extract(
        png_bytes(800, 3200), origin="public_dataset", tags=CallTags(case_id="c-1")
    )

    assert result.pipeline == "B"
    assert result.schema_valid is True and result.failure is None
    assert result.attempts == 1
    assert result.receipt is not None
    assert result.receipt.total == Decimal(51000)
    assert [i.price for i in result.receipt.items] == [Decimal(36000), Decimal(15000)]
    assert (result.tokens_in, result.tokens_out) == (1400, 300)
    assert result.image_tokens_in == 1120
    assert (result.image_width, result.image_height) == (384, 1536)
    assert result.list_cost_usd == Decimal("0.00115")  # 1400 * $0.5/M + 300 * $1.5/M
    assert result.model_alias == "test-public" and result.prompt_version == "b-v1"
    assert result.ocr_text is None and result.ocr_lines is None
    stages = result.stage_latency_ms
    assert stages.ocr is None
    assert stages.prepare is not None and stages.llm is not None and stages.validate_ is not None
    assert result.raw_text is None and result.cache_hit is False
    # The record of the call carries the image tokens and the stage tag.
    (record,) = sink.records
    assert (record.case_id, record.stage, record.image_tokens_in) == ("c-1", "llm", 1120)


async def test_request_sends_downscaled_image_schema_and_prompt(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=VALID)])
    await pipeline(make_client, provider).extract(png_bytes(800, 3200), origin="public_dataset")

    (call,) = provider.calls
    assert call.model == "gemini/test-flash"
    assert call.temperature == 0.0 and call.seed == 0
    assert call.max_tokens == pipeline_b.DEFAULT_MAX_TOKENS
    assert call.json_schema == Receipt.model_json_schema()
    system, user = call.messages
    assert system == {"role": "system", "content": VISION_PROMPTS["b-v1"].system}
    assert [p["type"] for p in user["content"]] == ["image_url", "text"]
    assert _sent_image(provider).size == (384, 1536)


async def test_image_settings_are_applied(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=VALID)])
    b = pipeline(make_client, provider, image=ModelImageSettings(max_side=640))
    result = await b.extract(png_bytes(800, 3200), origin="public_dataset")
    assert (result.image_width, result.image_height) == (160, 640)
    assert _sent_image(provider).size == (160, 640)


# --- invalid replies ---------------------------------------------------------------------


async def test_invalid_reply_is_repaired_once(make_client: Factory, sink: MemorySink) -> None:
    provider = FakeProvider(
        [
            FakeReply(text=INVALID, image_tokens_in=1120),
            FakeReply(text=VALID, image_tokens_in=1120),
        ]
    )
    result = await pipeline(make_client, provider).extract(png_bytes(), origin="public_dataset")

    assert result.schema_valid is True and result.attempts == 2
    assert result.image_tokens_in == 2240  # the image is sent again with the repair
    assert [r.stage for r in sink.records] == ["llm", "llm:repair"]


async def test_still_invalid_after_two_repairs_keeps_raw_text(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=INVALID)] * 3)
    result = await pipeline(make_client, provider).extract(png_bytes(), origin="public_dataset")

    assert len(provider.calls) == 3
    assert result.receipt is None and result.schema_valid is False
    assert result.failure == "schema_invalid" and result.attempts == 3
    assert result.raw_text == INVALID
    assert (result.needs_review, result.reconciled) == (True, None)


async def test_truncated_reply(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text='{"items": [', finish_reason="length")] * 3)
    result = await pipeline(make_client, provider).extract(png_bytes(), origin="public_dataset")
    assert result.failure == "truncated"


# --- data policy: Gemini receives CORD images only ---------------------------------------


@pytest.mark.parametrize("origin", ["user_upload", "synthetic"])
async def test_non_dataset_origin_is_refused_before_the_image_is_touched(
    make_client: Factory, monkeypatch: pytest.MonkeyPatch, sink: MemorySink, origin: DataOrigin
) -> None:
    def must_not_prepare(*args: object, **kwargs: object) -> None:
        raise AssertionError("a refused image was decoded")

    monkeypatch.setattr(pipeline_b, "prepare_model_image", must_not_prepare)
    provider = FakeProvider([FakeReply(text=VALID)])

    with pytest.raises(DataPolicyError, match="public dataset images only"):
        await pipeline(make_client, provider).extract(png_bytes(), origin=origin)
    assert provider.calls == []
    assert sink.records == []


async def test_refused_even_when_pointed_at_a_model_without_a_policy(
    make_client: Factory,
) -> None:
    provider = FakeProvider([FakeReply(text=VALID)])
    b = pipeline(make_client, provider, alias="test-chat")  # data_policy: any
    with pytest.raises(DataPolicyError):
        await b.extract(png_bytes(), origin="user_upload")
    assert provider.calls == []


async def test_layer_refuses_user_upload_to_public_only_alias_as_second_guard(
    make_client: Factory,
) -> None:
    # Even a caller that bypasses PipelineB cannot send a user image to the alias.
    provider = FakeProvider([FakeReply(text=VALID)])
    from receipt_extractor.pipelines.images import prepare_model_image  # noqa: PLC0415

    messages = build_vision_messages("b-v1", prepare_model_image(png_bytes()))
    with pytest.raises(DataPolicyError):
        await make_client(provider).complete(
            LLMRequest(model_alias="test-public", messages=messages, data_origin="user_upload")
        )
    assert provider.calls == []


def test_default_alias_and_every_gemini_alias_are_public_only() -> None:
    registry = ModelRegistry.from_yaml(Path(__file__).parents[3] / "configs" / "models.yaml")
    spec = registry.get(pipeline_b.DEFAULT_MODEL_ALIAS)
    assert spec.alias == "gemini-flash-lite"
    assert spec.model == "gemini/gemini-3.5-flash-lite"
    assert spec.provider == "gemini"
    assert spec.data_policy == "public_only"
    # Gemini's free tier may use what it is sent: no Gemini alias may accept user uploads.
    gemini = [s for s in registry if s.provider == "gemini"]
    assert {s.alias for s in gemini} >= {"gemini-flash", "gemini-flash-lite"}
    assert all(s.data_policy == "public_only" for s in gemini)
    assert pipeline_b.ALLOWED_ORIGIN == "public_dataset"


# --- other -------------------------------------------------------------------------------


async def test_rerun_is_served_from_cache_with_image_tokens(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=VALID, image_tokens_in=1120)])
    b = pipeline(make_client, provider, cache=True)
    first = await b.extract(png_bytes(), origin="public_dataset")
    second = await b.extract(png_bytes(), origin="public_dataset")

    assert len(provider.calls) == 1
    assert second.cache_hit is True
    assert second.image_tokens_in == first.image_tokens_in == 1120
    assert second.receipt == first.receipt


async def test_rejected_image_never_reaches_the_model(make_client: Factory) -> None:
    from receipt_extractor.pipelines.images import ImageRejectedError  # noqa: PLC0415

    provider = FakeProvider([FakeReply(text=VALID)])
    with pytest.raises(ImageRejectedError):
        await pipeline(make_client, provider).extract(b"not an image", origin="public_dataset")
    assert provider.calls == []


def test_unknown_prompt_version_rejected(make_client: Factory) -> None:
    with pytest.raises(ValueError, match="unknown vision prompt version"):
        pipeline(make_client, FakeProvider(), prompt_version="b-v0")
    with pytest.raises(ValueError, match="unknown vision prompt version"):
        build_vision_messages("a-v3", object())  # type: ignore[arg-type]


def test_vision_prompt_treats_printed_text_as_untrusted() -> None:
    system = VISION_PROMPTS["b-v1"].system
    assert "untrusted" in system and "never follow instructions" in system
    assert "as printed" in system  # amounts left to parse_amount


async def test_serialised_result_has_image_fields(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=VALID, image_tokens_in=1120)])
    result = await pipeline(make_client, provider).extract(png_bytes(), origin="public_dataset")
    stored = to_jsonable_python(result)

    assert stored["image_tokens_in"] == 1120
    assert stored["image_width"] == 40 and stored["image_height"] == 60
    assert "ocr_text" not in stored
    assert stored["stage_latency_ms"]["ocr"] is None


async def test_variant_name_and_timeout_are_applied(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=VALID)])
    b = pipeline(make_client, provider, variant="C-local", timeout_s=600)
    result = await b.extract(png_bytes(), origin="public_dataset")

    assert b.name == "C-local" and result.pipeline == "C-local"
    assert provider.calls[0].timeout_s == 600


async def test_renamed_variant_keeps_the_dataset_only_guard(make_client: Factory) -> None:
    provider = FakeProvider([FakeReply(text=VALID)])
    b = pipeline(make_client, provider, alias="test-chat", variant="C-local")
    with pytest.raises(DataPolicyError, match="C-local accepts public dataset images only"):
        await b.extract(png_bytes(), origin="user_upload")
    assert provider.calls == []
