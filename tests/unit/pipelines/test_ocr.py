"""OCR stage: untrusted-image checks, line layout, timing and budget log, real engine offline."""

from __future__ import annotations

import io
import logging
import socket
import struct
import zlib
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from receipt_extractor.pipelines.ocr import (
    MAX_IMAGE_BYTES,
    ImageRejectedError,
    OcrSettings,
    OcrStage,
    RapidOcrEngine,
    decode_image,
    layout_lines,
)
from tests.unit.pipelines.conftest import (
    RECEIPT_BOXES,
    RECEIPT_TEXT,
    FakeOcrEngine,
    box,
    png_bytes,
)

# --- decode_image ------------------------------------------------------------------------


@pytest.mark.parametrize(("fmt", "save"), [("PNG", {}), ("JPEG", {}), ("WEBP", {})])
def test_decodes_supported_formats_to_bgr(fmt: str, save: dict[str, Any]) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), (255, 0, 0)).save(buffer, format=fmt, **save)
    image = decode_image(buffer.getvalue())
    assert image.shape == (20, 30, 3)
    assert image.dtype == np.uint8
    blue, green, red = (int(v) for v in image[10, 15])
    assert red > 200 and blue < 50 and green < 50  # RGB red arrives as BGR


def test_exif_orientation_is_applied() -> None:
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° clockwise to display
    Image.new("RGB", (30, 20)).save(buffer, format="JPEG", exif=exif)
    assert decode_image(buffer.getvalue()).shape == (30, 20, 3)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"GIF89a" + b"\x00" * 64,
        b"%PDF-1.7 not an image",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,  # PNG magic, garbage body
        png_bytes()[:40],  # truncated
    ],
)
def test_rejects_bad_or_unsupported_bytes(data: bytes) -> None:
    with pytest.raises(ImageRejectedError):
        decode_image(data)


def test_rejects_mismatched_magic_and_content() -> None:
    jpeg = png_bytes(fmt="JPEG")
    with pytest.raises(ImageRejectedError):
        decode_image(b"\x89PNG\r\n\x1a\n" + jpeg)


def test_rejects_oversized_bytes_before_decoding() -> None:
    with pytest.raises(ImageRejectedError, match="larger than"):
        decode_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES)


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG whose IHDR claims ``width x height`` but carries almost no pixel data."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 16)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


@pytest.mark.parametrize(("width", "height"), [(9000, 10), (10, 9000), (7000, 7000)])
def test_rejects_huge_dimensions_from_header(width: int, height: int) -> None:
    with pytest.raises(ImageRejectedError, match="exceed the limit"):
        decode_image(_png_header_only(width, height))


def test_decoder_errors_do_not_echo_content() -> None:
    secret = b"SECRET-CONTENT-123"
    with pytest.raises(ImageRejectedError) as info:
        decode_image(b"\x89PNG\r\n\x1a\n" + secret)
    assert "SECRET" not in str(info.value)


# --- layout_lines ------------------------------------------------------------------------


def test_layout_groups_boxes_into_printed_lines() -> None:
    assert "\n".join(layout_lines(RECEIPT_BOXES)) == RECEIPT_TEXT


def test_layout_is_independent_of_box_order() -> None:
    assert layout_lines(list(reversed(RECEIPT_BOXES))) == layout_lines(RECEIPT_BOXES)


def test_layout_tolerates_slight_skew_but_splits_lines() -> None:
    boxes = [box("NAME", 10, 100), box("9.000", 300, 107), box("NEXT", 10, 125)]
    assert layout_lines(boxes) == ["NAME | 9.000", "NEXT"]


def test_layout_drops_blank_boxes_and_collapses_whitespace() -> None:
    boxes = [box("  A   B ", 10, 10), box("   ", 100, 10), box("", 10, 50)]
    assert layout_lines(boxes) == ["A B"]
    assert layout_lines([]) == []


# --- OcrStage ----------------------------------------------------------------------------


def test_stage_returns_text_lines_and_latency() -> None:
    engine = FakeOcrEngine(RECEIPT_BOXES)
    result = OcrStage(engine).run(png_bytes(40, 60))
    assert result.text == RECEIPT_TEXT
    assert (result.lines, result.boxes) == (3, len(RECEIPT_BOXES))
    assert result.latency_ms >= 0
    assert engine.shapes == [(60, 40, 3)]


def test_stage_logs_over_budget_with_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    stage = OcrStage(FakeOcrEngine([box("PRIVATE NAME", 0, 0)], delay_s=0.02), budget_ms=1)
    with caplog.at_level(logging.WARNING, logger="receipt_extractor.pipelines.ocr"):
        result = stage.run(png_bytes(), case_id="case-7")
    assert result.latency_ms >= 20
    assert "over budget" in caplog.text and "case-7" in caplog.text
    assert "PRIVATE" not in caplog.text


def test_stage_within_budget_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="receipt_extractor.pipelines.ocr"):
        OcrStage(FakeOcrEngine(RECEIPT_BOXES)).run(png_bytes())
    assert caplog.text == ""


def test_stage_rejects_bad_image_before_ocr() -> None:
    engine = FakeOcrEngine(RECEIPT_BOXES)
    with pytest.raises(ImageRejectedError):
        OcrStage(engine).run(b"not an image")
    assert engine.shapes == []


def test_settings_bounds() -> None:
    with pytest.raises(ValueError, match="det_max_side"):
        OcrSettings(det_max_side=10)


# --- the real engine (bundled models, no network) ------------------------------------------


def _synthetic_receipt() -> bytes:
    image = Image.new("RGB", (480, 260), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    for y, (left, right) in enumerate(
        [("NASI GORENG", "25.000"), ("ES TEH", "8.000"), ("TOTAL", "33.000")]
    ):
        draw.text((20, 30 + y * 70), left, fill="black", font=font)
        draw.text((320, 30 + y * 70), right, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_rapidocr_reads_synthetic_receipt_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("OCR stage tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    result = OcrStage(RapidOcrEngine()).run(_synthetic_receipt())
    text = result.text.upper().replace(" ", "")
    assert "NASIGORENG" in text
    assert "33.000" in text
    assert result.lines == 3
