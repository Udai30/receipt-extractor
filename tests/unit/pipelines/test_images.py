"""Image preparation for vision models: downscale, re-encode, metadata stripped, checks kept."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from receipt_extractor.pipelines.images import (
    MAX_IMAGE_BYTES,
    ImageRejectedError,
    ModelImageSettings,
    load_upright_rgb,
    prepare_model_image,
)
from tests.unit.pipelines.conftest import png_bytes


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_tall_receipt_is_downscaled_on_its_long_side() -> None:
    prepared = prepare_model_image(png_bytes(1000, 4000))

    assert (prepared.source_width, prepared.source_height) == (1000, 4000)
    assert (prepared.width, prepared.height) == (384, 1536)
    assert prepared.mime_type == "image/jpeg"
    sent = _open(prepared.data)
    assert sent.format == "JPEG" and sent.size == (384, 1536)


def test_small_image_is_never_upscaled() -> None:
    prepared = prepare_model_image(png_bytes(300, 500))
    assert (prepared.width, prepared.height) == (300, 500)


def test_png_output_and_custom_max_side() -> None:
    prepared = prepare_model_image(
        png_bytes(2000, 1000), ModelImageSettings(max_side=512, format="png")
    )
    assert prepared.mime_type == "image/png"
    assert _open(prepared.data).size == (512, 256)


def test_webp_and_jpeg_sources_are_accepted() -> None:
    for fmt in ("WEBP", "JPEG"):
        prepared = prepare_model_image(png_bytes(64, 96, fmt=fmt))
        assert (prepared.width, prepared.height) == (64, 96)


def test_exif_rotation_applied_and_metadata_stripped() -> None:
    image = Image.new("RGB", (200, 100), (255, 255, 255))
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise to display
    exif[0x010F] = "SecretCam"  # camera make
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)

    prepared = prepare_model_image(buffer.getvalue())

    assert (prepared.width, prepared.height) == (100, 200)
    sent = _open(prepared.data)
    assert not sent.getexif()
    assert b"SecretCam" not in prepared.data


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", b"GIF89a" + b"\x00" * 20, b"\x89PNG\r\n\x1a\n" + b"\x00" * 30],
)
def test_bad_bytes_are_rejected(data: bytes) -> None:
    with pytest.raises(ImageRejectedError):
        prepare_model_image(data)


def test_oversized_bytes_rejected_before_decoding() -> None:
    with pytest.raises(ImageRejectedError, match="larger than"):
        load_upright_rgb(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES)


def test_huge_dimensions_rejected_from_the_header() -> None:
    # A 9000 x 10 PNG is tiny on disk but over the per-side limit.
    with pytest.raises(ImageRejectedError, match="exceed the limit"):
        prepare_model_image(png_bytes(9000, 10))


@pytest.mark.parametrize("bad", [{"max_side": 100}, {"max_side": 5000}, {"jpeg_quality": 10}])
def test_settings_are_bounded(bad: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        ModelImageSettings.model_validate(bad)
