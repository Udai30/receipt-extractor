"""Smoke tests of the real Gradio app through ``gradio_client`` (local server, fake pipeline A).

Covers the M3.2 testing requirement: a sample receipt, oversized uploads rejected, and the
budget-exhausted banner — plus uploads reaching pipeline A only, as ``user_upload``.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from receipt_extractor.ui.app import AppServices, build_app, launch_options
from receipt_extractor.ui.live import DemoBudget, LiveService
from receipt_extractor.ui.showcase import load_showcase
from tests.unit.ui.test_live import FakePipeline

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
gradio_client = pytest.importorskip("gradio_client")

# Gradio's in-process server (uvicorn on a background thread, Windows proactor loop) leaves its
# own sockets/loop to the garbage collector; those warnings are not from this project's code.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

# Outputs: banner, sample image, then 7 per column (title, badge, items, totals, meta, raw, ocr).
BANNER, A_BADGE, B_BADGE, A_ITEMS = 0, 3, 10, 4


def png_file(directory: Path, name: str, width: int = 60, height: int = 90) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    path = directory / name
    path.write_bytes(buffer.getvalue())
    return str(path)


def launch(services: AppServices) -> tuple[Any, Any]:
    demo = build_app(services)
    options = launch_options()
    _, url, _ = demo.launch(
        prevent_thread_lock=True, server_name="127.0.0.1", quiet=True, **options
    )
    return demo, gradio_client.Client(url, verbose=False)


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[Any, FakePipeline, Path]]:
    pipeline = FakePipeline()
    reports = Path(__file__).resolve().parents[3] / "reports"
    services = AppServices(
        reports_dir=reports,
        live=LiveService(lambda: pipeline, budget=DemoBudget(None)),
        showcase=load_showcase(),
    )
    demo, client = launch(services)
    try:
        yield client, pipeline, reports
    finally:
        demo.close()


def test_sample_receipt_shows_cached_a_and_b(app: tuple[Any, FakePipeline, Path]) -> None:
    client, pipeline, _ = app
    out = client.predict("validation-0005", api_name="/sample")
    assert "validation-0005" in out[BANNER] and "cached results" in out[BANNER]
    assert out[A_BADGE].strip("*") and out[B_BADGE].strip("*")
    assert out[A_ITEMS]["data"], "pipeline A's line items are shown"
    assert pipeline.calls == []  # samples never call a model


def test_upload_goes_to_pipeline_a_only(
    app: tuple[Any, FakePipeline, Path], tmp_path: Path
) -> None:
    client, pipeline, _ = app
    before = len(pipeline.calls)
    out = client.predict(
        gradio_client.handle_file(png_file(tmp_path, "r.png")), api_name="/extract"
    )
    assert "read live by pipeline A" in out[BANNER]
    assert "Totals add up" in out[A_BADGE]
    assert "sample receipts only" in out[B_BADGE]  # B never runs on uploads
    assert pipeline.calls[before:] == ["user_upload"]


def test_too_many_pixels_rejected_server_side(
    app: tuple[Any, FakePipeline, Path], tmp_path: Path
) -> None:
    client, pipeline, _ = app
    before = len(pipeline.calls)
    out = client.predict(
        gradio_client.handle_file(png_file(tmp_path, "wide.png", 4200, 20)), api_name="/extract"
    )
    assert "4096 px per side" in out[BANNER]
    assert len(pipeline.calls) == before


def test_upload_over_5_mb_is_refused(app: tuple[Any, FakePipeline, Path], tmp_path: Path) -> None:
    client, pipeline, _ = app
    before = len(pipeline.calls)
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(5 * 1024 * 1024 + 100))
    with pytest.raises(Exception, match=r"(?i)file.*(large|size)|413|exceed"):
        client.predict(gradio_client.handle_file(str(big)), api_name="/extract")
    assert len(pipeline.calls) == before


def test_budget_exhausted_banner(tmp_path: Path) -> None:
    pipeline = FakePipeline()
    services = AppServices(
        reports_dir=tmp_path,  # no reports yet: Results/Failures show their empty states
        live=LiveService(lambda: pipeline, budget=DemoBudget(0)),
        showcase=load_showcase(),
    )
    demo, client = launch(services)
    try:
        out = client.predict(
            gradio_client.handle_file(png_file(tmp_path, "r.png")), api_name="/extract"
        )
        assert out[BANNER].startswith("⏸") and "paused for today" in out[BANNER]
        assert pipeline.calls == []
        sample = client.predict("validation-0033", api_name="/sample")
        assert "validation-0033" in sample[BANNER]  # samples still work
    finally:
        demo.close()
