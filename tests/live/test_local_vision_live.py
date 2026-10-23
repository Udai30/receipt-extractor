"""Live smoke test of the local vision variant: a CORD image → Ollama Qwen3-VL-4B on the CPU.

Opt-in (``-m live``). Skips unless Ollama answers at ``OLLAMA_BASE_URL`` with the alias's model
pulled, and unless ``cli data prepare`` has run. Cache off; the first call also loads the model.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from receipt_extractor.config import get_settings
from receipt_extractor.data.cord import load_manifest, read_gold
from receipt_extractor.llm import MemorySink, build_client
from receipt_extractor.pipelines.images import ModelImageSettings
from receipt_extractor.pipelines.pipeline_b import PipelineB

pytestmark = pytest.mark.live

CORD_DIR = Path(__file__).resolve().parents[2] / "data" / "cord"
CASE_ID = "validation-0000"
ALIAS = "ollama-qwen3-vl-4b"


async def test_live_local_vision_extracts_a_cord_receipt() -> None:
    settings = get_settings()
    sink = MemorySink()
    client = build_client(settings, cache=False, sinks=[sink])
    model = client.registry.get(ALIAS).model.split("/", 1)[1]
    base = str(settings.ollama_base_url).rstrip("/")
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=5).json()
    except httpx.HTTPError:
        pytest.skip("Ollama is not running")
    if model not in {m.get("name") for m in tags.get("models", [])}:
        pytest.skip(f"`ollama pull {model}` first")
    try:
        manifest = load_manifest(CORD_DIR)
    except (OSError, ValueError):
        pytest.skip("CORD not prepared (uv run receipt_extractor data prepare)")
    entry = manifest["cases"][CASE_ID]
    image = (CORD_DIR / entry["image"]).read_bytes()
    assert hashlib.sha256(image).hexdigest() == entry["image_sha256"]
    gold = read_gold(CORD_DIR, manifest, CASE_ID)

    pipeline = PipelineB(
        client=client,
        model_alias=ALIAS,
        max_tokens=4096,
        image=ModelImageSettings(max_side=1024),
        seed=0,
        variant="C-local",
        timeout_s=600,
    )
    result = await pipeline.extract(image, origin="public_dataset")

    assert result.pipeline == "C-local"
    assert result.schema_valid, result.raw_text
    assert result.receipt is not None and result.receipt.items
    assert result.receipt.total == gold.total
    assert result.list_cost_usd == 0
    assert sink.records and all(r.error is None for r in sink.records)
