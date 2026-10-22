"""Live smoke test of pipeline A: real RapidOCR + real Groq ``gpt-oss-20b`` (opt-in, ``-m live``).

The image is a synthetic receipt rendered in the test (no dataset or user content); one call per
mode, cache off so the provider is really exercised. Skips without ``GROQ_API_KEY``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from receipt_extractor.config import get_settings
from receipt_extractor.llm import MemorySink, build_client
from receipt_extractor.pipelines.ocr import OcrStage, RapidOcrEngine
from receipt_extractor.pipelines.pipeline_a import Mode, PipelineA
from tests.unit.pipelines.test_ocr import _synthetic_receipt

pytestmark = pytest.mark.live


@pytest.mark.parametrize("mode", ["naive", "engineered"])
async def test_live_pipeline_a_extracts_synthetic_receipt(mode: Mode) -> None:
    settings = get_settings()
    if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
        pytest.skip("GROQ_API_KEY not set")
    sink = MemorySink()
    pipeline = PipelineA(
        client=build_client(settings, cache=False, sinks=[sink]),
        ocr=OcrStage(RapidOcrEngine()),
        mode=mode,
        seed=0,
    )
    result = await pipeline.extract(_synthetic_receipt(), origin="synthetic")

    assert result.schema_valid, result.raw_text
    assert result.receipt is not None
    assert result.receipt.total == Decimal(33000)
    assert {item.price for item in result.receipt.items} == {Decimal(25000), Decimal(8000)}
    assert result.stage_latency_ms.ocr is not None and result.stage_latency_ms.llm
    assert result.tokens_in > 0 and result.list_cost_usd > 0
    assert sink.records and all(r.error is None for r in sink.records)
