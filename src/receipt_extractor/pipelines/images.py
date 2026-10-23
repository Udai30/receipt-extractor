"""Untrusted receipt images: checks before decoding, and the downscaled copy a vision model sees.

Shared by pipeline A's OCR stage and pipeline B. Image bytes are untrusted (a user's upload or a
dataset file): size, format (PNG/JPEG/WebP magic) and dimensions are checked *before* decoding,
so a decompression bomb is rejected from its header; EXIF orientation is applied; everything
happens in memory and nothing is written or logged. Decoder messages are never echoed (they can
quote file content). Pillow is imported on first use, never at module import.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from PIL import Image as PILImage

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "MAX_IMAGE_SIDE",
    "ImageRejectedError",
    "ModelImage",
    "ModelImageSettings",
    "load_upright_rgb",
    "prepare_model_image",
]

MAX_IMAGE_BYTES: Final = 32 * 1024 * 1024
MAX_IMAGE_SIDE: Final = 8192
MAX_IMAGE_PIXELS: Final = 40_000_000
# What a provider accepts inline (llm.types.MAX_IMAGE_BYTES); the encoded copy must fit.
MAX_MODEL_IMAGE_BYTES: Final = 8 * 1024 * 1024
_MAGIC: Final = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
)


class ImageRejectedError(ValueError):
    """The image is too large, not a supported format, or cannot be decoded."""


def _sniff_format(data: bytes) -> str:
    for magic, name in _MAGIC:
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    raise ImageRejectedError("unsupported image format (PNG, JPEG or WebP expected)")


def load_upright_rgb(data: bytes) -> PILImage.Image:
    """Validate and decode ``data`` into an upright RGB Pillow image (in memory only)."""
    from PIL import Image, ImageOps  # noqa: PLC0415 - heavy import stays out of module import

    if not data:
        raise ImageRejectedError("empty image")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRejectedError(f"image larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    expected = _sniff_format(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=[expected]) as image:
                width, height = image.size  # header only; nothing decoded yet
                if max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
                    raise ImageRejectedError(
                        f"image dimensions {width}x{height} exceed the limit "
                        f"({MAX_IMAGE_SIDE} px per side, {MAX_IMAGE_PIXELS:,} px)"
                    )
                return ImageOps.exif_transpose(image).convert("RGB")
    except ImageRejectedError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Warning):
        raise ImageRejectedError("image could not be decoded") from None


class ModelImageSettings(BaseModel):
    """How an image is resized and encoded before a vision model sees it (a run param).

    Receipts are tall with small print, so the long side is capped rather than the short one;
    images are never upscaled. JPEG keeps the payload small; quality 90 keeps print edges sharp.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_side: int = Field(default=1536, ge=256, le=4096)
    format: Literal["jpeg", "png"] = "jpeg"
    jpeg_quality: int = Field(default=90, ge=50, le=100)


@dataclass(frozen=True, slots=True)
class ModelImage:
    """The encoded copy sent to the model, with its size before and after resizing."""

    data: bytes
    mime_type: Literal["image/jpeg", "image/png"]
    width: int
    height: int
    source_width: int
    source_height: int


def prepare_model_image(data: bytes, settings: ModelImageSettings | None = None) -> ModelImage:
    """Validate ``data``, downscale it to ``settings.max_side`` and re-encode it in memory.

    Re-encoding also drops metadata (EXIF, GPS, comments) from what leaves the machine.
    """
    from PIL import Image  # noqa: PLC0415

    settings = settings or ModelImageSettings()
    image = load_upright_rgb(data)
    source_width, source_height = image.size
    scale = settings.max_side / max(source_width, source_height)
    if scale < 1:
        size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    if settings.format == "jpeg":
        image.save(buffer, format="JPEG", quality=settings.jpeg_quality, optimize=True)
        mime: Literal["image/jpeg", "image/png"] = "image/jpeg"
    else:
        image.save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    encoded = buffer.getvalue()
    if len(encoded) > MAX_MODEL_IMAGE_BYTES:
        raise ImageRejectedError("encoded image too large for the model; lower max_side")
    width, height = image.size
    return ModelImage(
        data=encoded,
        mime_type=mime,
        width=width,
        height=height,
        source_width=source_width,
        source_height=source_height,
    )
