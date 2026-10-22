"""Pipeline A's OCR stage: RapidOCR (ONNX Runtime, CPU) → layout-ordered text (TRD §3, §6).

- **Input.** Image bytes are untrusted: size, format (PNG/JPEG/WebP magic) and dimensions are
  checked *before* decoding, so a decompression bomb is rejected from its header. Images are
  decoded in memory and never written anywhere.
- **Models.** Only the ONNX models shipped inside the ``rapidocr`` wheel are used, passed by
  explicit path, so RapidOCR's own download branch can never run (CLAUDE.md §3). The engine is
  built lazily on first use, never at import time.
- **Timing.** One engine call at a time per :class:`OcrStage` (ONNX Runtime already uses several
  threads per call); ``latency_ms`` is the engine time, excluding the wait for the lock, so it
  measures the stage rather than queueing. Above :data:`OCR_BUDGET_MS` a warning is logged
  (metadata only).
- **Layout.** Boxes are grouped into printed lines by vertical overlap and joined left to right
  with `` | ``, so a name and its price stay on one line for the LLM.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "MAX_IMAGE_SIDE",
    "OCR_BUDGET_MS",
    "ImageRejectedError",
    "OcrBox",
    "OcrEngine",
    "OcrError",
    "OcrResult",
    "OcrSettings",
    "OcrStage",
    "RapidOcrEngine",
    "decode_image",
    "layout_lines",
]

OCR_BUDGET_MS: Final = 2000  # TRD §6: RapidOCR per receipt on the laptop CPU
MAX_IMAGE_BYTES: Final = 32 * 1024 * 1024
MAX_IMAGE_SIDE: Final = 8192
MAX_IMAGE_PIXELS: Final = 40_000_000
LINE_SEPARATOR: Final = " | "
# Shipped in the rapidocr wheel (3.x); checked on engine start.
_DET_MODEL: Final = "PP-OCRv6_det_small.onnx"
_REC_MODEL: Final = "PP-OCRv6_rec_small.onnx"
_MAGIC: Final = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
)


class OcrError(RuntimeError):
    """The OCR engine could not be started or failed on an image."""


class ImageRejectedError(ValueError):
    """The image is too large, not a supported format, or cannot be decoded."""


class OcrSettings(BaseModel):
    """Engine settings (chosen on CORD validation, WORKLOG M1.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    det_max_side: int = Field(default=960, ge=320, le=4096)
    use_angle_cls: bool = False
    threads: int = Field(default=4, ge=1, le=64)
    min_text_score: float = Field(default=0.5, ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class OcrBox:
    """One recognised text box: its quadrilateral (x, y points), text and confidence."""

    points: tuple[tuple[float, float], ...]
    text: str
    score: float

    @property
    def top(self) -> float:
        return min(y for _, y in self.points)

    @property
    def bottom(self) -> float:
        return max(y for _, y in self.points)

    @property
    def left(self) -> float:
        return min(x for x, _ in self.points)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    lines: int
    boxes: int
    latency_ms: int


class OcrEngine(Protocol):
    """Reads text boxes from a decoded BGR image (``uint8``, H x W x 3)."""

    def read(self, image: npt.NDArray[np.uint8]) -> list[OcrBox]: ...


def _sniff_format(data: bytes) -> str:
    for magic, name in _MAGIC:
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    raise ImageRejectedError("unsupported image format (PNG, JPEG or WebP expected)")


def decode_image(data: bytes) -> npt.NDArray[np.uint8]:
    """Validate and decode ``data`` into an upright BGR ``uint8`` array (in memory only)."""
    import numpy as np  # noqa: PLC0415 - heavy imports stay out of module import
    from PIL import Image, ImageOps  # noqa: PLC0415

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
                upright = ImageOps.exif_transpose(image).convert("RGB")
    except ImageRejectedError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Warning):
        # Never echo decoder messages: they can quote file content.
        raise ImageRejectedError("image could not be decoded") from None
    rgb = np.asarray(upright, dtype=np.uint8)
    return np.ascontiguousarray(rgb[:, :, ::-1])


def layout_lines(boxes: Sequence[OcrBox]) -> list[str]:
    """Group boxes into printed lines (top to bottom), each joined left to right.

    A box joins the current line when its vertical centre lies within half a typical box
    height of the line's centre; otherwise it starts a new line.
    """
    kept = [box for box in boxes if box.text.strip()]
    if not kept:
        return []
    typical = median(box.height for box in kept)
    lines: list[list[OcrBox]] = []
    centres: list[float] = []
    for box in sorted(kept, key=lambda b: (b.center_y, b.left)):
        if lines and abs(box.center_y - centres[-1]) <= typical / 2:
            lines[-1].append(box)
            members = lines[-1]
            centres[-1] = sum(b.center_y for b in members) / len(members)
        else:
            lines.append([box])
            centres.append(box.center_y)
    return [
        LINE_SEPARATOR.join(" ".join(b.text.split()) for b in sorted(line, key=lambda b: b.left))
        for line in lines
    ]


class RapidOcrEngine:
    """RapidOCR on ONNX Runtime with the wheel's bundled models (no network, ever)."""

    def __init__(self, settings: OcrSettings | None = None) -> None:
        self.settings = settings or OcrSettings()
        self._engine: Any = None
        self._lock = threading.Lock()

    def _build(self) -> Any:
        try:
            import rapidocr  # noqa: PLC0415 - heavy (onnxruntime, opencv)
            from rapidocr import RapidOCR  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise OcrError("rapidocr is not installed; run `uv sync`") from exc
        models = Path(rapidocr.__file__).resolve().parent / "models"
        det, rec = models / _DET_MODEL, models / _REC_MODEL
        missing = [p.name for p in (det, rec) if not p.is_file()]
        if missing:
            raise OcrError(f"bundled RapidOCR model(s) missing: {', '.join(missing)}")
        settings = self.settings
        params: dict[str, Any] = {
            "Global.log_level": "warning",
            "Global.use_cls": settings.use_angle_cls,
            "Global.text_score": settings.min_text_score,
            "Global.model_root_dir": str(models),
            "Det.model_path": str(det),
            "Det.limit_type": "max",
            "Det.limit_side_len": settings.det_max_side,
            "Rec.model_path": str(rec),
            "EngineConfig.onnxruntime.intra_op_num_threads": settings.threads,
        }
        if settings.use_angle_cls:
            cls_models = sorted(models.glob("*cls*.onnx"))
            if not cls_models:
                raise OcrError("bundled RapidOCR angle-classifier model missing")
            params["Cls.model_path"] = str(cls_models[0])
        try:
            return RapidOCR(params=params)
        except Exception as exc:
            raise OcrError(f"RapidOCR failed to start: {type(exc).__name__}") from None

    def read(self, image: npt.NDArray[np.uint8]) -> list[OcrBox]:
        with self._lock:
            if self._engine is None:
                self._engine = self._build()
        try:
            output = self._engine(image)
        except Exception as exc:
            raise OcrError(f"RapidOCR failed: {type(exc).__name__}") from None
        if output.boxes is None or output.txts is None or output.scores is None:
            return []
        return [
            OcrBox(
                points=tuple((float(x), float(y)) for x, y in quad),
                text=str(text),
                score=float(score),
            )
            for quad, text, score in zip(output.boxes, output.txts, output.scores, strict=True)
        ]


class OcrStage:
    """Image bytes → layout-ordered text, timed, one engine call at a time."""

    def __init__(self, engine: OcrEngine, *, budget_ms: int = OCR_BUDGET_MS) -> None:
        self._engine = engine
        self._budget_ms = budget_ms
        self._lock = threading.Lock()

    def run(self, data: bytes, *, case_id: str | None = None) -> OcrResult:
        """Blocking; call it through ``asyncio.to_thread`` from async code."""
        image = decode_image(data)
        with self._lock:
            start = time.perf_counter()
            boxes = self._engine.read(image)
            latency_ms = max(0, round((time.perf_counter() - start) * 1000))
        if latency_ms > self._budget_ms:
            logger.warning(
                "ocr stage over budget: %d ms > %d ms (case %s, %dx%d px)",
                latency_ms,
                self._budget_ms,
                case_id or "-",
                image.shape[1],
                image.shape[0],
            )
        lines = layout_lines(boxes)
        return OcrResult(
            text="\n".join(lines), lines=len(lines), boxes=len(boxes), latency_ms=latency_ms
        )
