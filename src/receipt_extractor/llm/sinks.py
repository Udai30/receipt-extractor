"""Destinations for ``llm_call`` records (TRD §S3 accounting).

- :class:`JSONLSink` — ``runs/<run_id>/llm_calls.jsonl`` during eval runs (BACKEND_SCHEMA §S2).
  One line is appended and flushed per call, so an interrupted run keeps its accounting.
  Write failures propagate: an eval run must not continue without its accounting.
- :class:`MemorySink` — keeps records in a list (tests, CLI).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Final, Protocol

from receipt_extractor.llm.types import LLMCallRecord

LLM_CALLS_FILENAME: Final = "llm_calls.jsonl"
_RUN_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CallSink(Protocol):
    async def write(self, record: LLMCallRecord) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    async def write(self, record: LLMCallRecord) -> None:
        self.records.append(record)


class JSONLSink:
    """Appends one JSON object per line to ``path``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    @classmethod
    def for_run(cls, runs_root: Path, run_id: str) -> JSONLSink:
        """Sink at ``<runs_root>/<run_id>/llm_calls.jsonl`` (``run_id`` must be a safe name)."""
        if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError("run_id must be letters, digits, '.', '_' or '-' (max 128)")
        return cls(runs_root / run_id / LLM_CALLS_FILENAME)

    async def write(self, record: LLMCallRecord) -> None:
        line = record.model_dump_json() + "\n"
        async with self._lock:  # keep lines whole when tasks write concurrently
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
