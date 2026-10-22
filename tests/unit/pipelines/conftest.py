"""Helpers for pipeline tests: tiny in-memory images and a scripted OCR engine (no RapidOCR)."""

from __future__ import annotations

import io
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from receipt_extractor.pipelines.ocr import OcrBox, OcrStage


def png_bytes(width: int = 40, height: int = 60, *, fmt: str = "PNG", **save: Any) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buffer, format=fmt, **save)
    return buffer.getvalue()


def box(text: str, left: float, top: float, width: float = 50, height: float = 20) -> OcrBox:
    right, bottom = left + width, top + height
    return OcrBox(
        points=((left, top), (right, top), (right, bottom), (left, bottom)),
        text=text,
        score=0.99,
    )


class FakeOcrEngine:
    """Returns fixed boxes; records the image shapes it was given."""

    def __init__(self, boxes: Sequence[OcrBox] = (), *, delay_s: float = 0.0) -> None:
        self.boxes = list(boxes)
        self.delay_s = delay_s
        self.shapes: list[tuple[int, ...]] = []

    def read(self, image: npt.NDArray[np.uint8]) -> list[OcrBox]:
        self.shapes.append(image.shape)
        if self.delay_s:
            time.sleep(self.delay_s)
        return list(self.boxes)


RECEIPT_BOXES = (
    box("KOPI SUSU", 10, 100, 120),
    box("2", 150, 102, 10),
    box("36.000", 200, 101, 60),
    box("ROTI BAKAR", 10, 130, 120),
    box("15.000", 200, 131, 60),
    box("TOTAL", 10, 170, 60),
    box("51.000", 200, 169, 60),
)
RECEIPT_TEXT = "KOPI SUSU | 2 | 36.000\nROTI BAKAR | 15.000\nTOTAL | 51.000"


@pytest.fixture
def receipt_ocr() -> OcrStage:
    return OcrStage(FakeOcrEngine(RECEIPT_BOXES))
