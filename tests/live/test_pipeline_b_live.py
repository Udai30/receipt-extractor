"""Live smoke test of pipeline B: a CORD validation image → real Gemini Flash (``-m live``).

B accepts public dataset images only, so the input is a real CORD receipt (never synthetic or
user content). One call, cache off so the provider is really exercised (it uses 1 of the alias's
daily requests). Skips without ``GEMINI_API_KEY`` or without ``cli data prepare``'s output.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from receipt_extractor.config import get_settings
from receipt_extractor.data.cord import load_manifest, read_gold
from receipt_extractor.llm import MemorySink, build_client
from receipt_extractor.pipelines.pipeline_b import PipelineB

pytestmark = pytest.mark.live

CORD_DIR = Path(__file__).resolve().parents[2] / "data" / "cord"
CASE_ID = "validation-0000"


async def test_live_pipeline_b_extracts_a_cord_receipt() -> None:
    settings = get_settings()
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
        pytest.skip("GEMINI_API_KEY not set")
    try:
        manifest = load_manifest(CORD_DIR)
    except (OSError, ValueError):
        pytest.skip("CORD not prepared (uv run receipt_extractor data prepare)")
    entry = manifest["cases"][CASE_ID]
    image = (CORD_DIR / entry["image"]).read_bytes()
    assert hashlib.sha256(image).hexdigest() == entry["image_sha256"]
    gold = read_gold(CORD_DIR, manifest, CASE_ID)

    sink = MemorySink()
    pipeline = PipelineB(client=build_client(settings, cache=False, sinks=[sink]), seed=0)
    result = await pipeline.extract(image, origin="public_dataset")

    assert result.schema_valid, result.raw_text
    assert result.receipt is not None
    assert result.receipt.total == gold.total
    assert result.receipt.items
    assert result.image_width and result.image_height
    assert max(result.image_width, result.image_height) <= pipeline.image_settings.max_side
    assert result.stage_latency_ms.prepare is not None and result.stage_latency_ms.llm
    assert result.tokens_in > 0 and result.list_cost_usd > 0
    # Gemini reports input tokens per modality; LiteLLM exposes the image part.
    assert result.image_tokens_in is not None and 0 < result.image_tokens_in < result.tokens_in
    assert sink.records and all(r.error is None for r in sink.records)
