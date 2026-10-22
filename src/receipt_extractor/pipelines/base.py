"""The pipeline protocol (TRD §4.2): image bytes in, :class:`ExtractionResult` out.

Pipelines never return ad-hoc dicts (CLAUDE.md §5). ``origin`` says where the image comes from
and is passed to every model call, so the LLM layer can refuse ``public_only`` models for user
uploads; ``tags`` correlate the calls with a run/case or a UI request.
"""

from __future__ import annotations

from typing import Protocol

from receipt_extractor.core.extraction import ExtractionResult
from receipt_extractor.llm import CallTags
from receipt_extractor.llm.types import DataOrigin

__all__ = ["Pipeline"]


class Pipeline(Protocol):
    @property
    def name(self) -> str: ...

    async def extract(
        self, image: bytes, *, origin: DataOrigin, tags: CallTags | None = None
    ) -> ExtractionResult: ...
