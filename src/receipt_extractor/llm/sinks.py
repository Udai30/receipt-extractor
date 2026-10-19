"""Destinations for ``llm_call`` records (TRD §S3 accounting).

- :class:`JSONLSink` — ``runs/<run_id>/llm_calls.jsonl`` during eval runs (BACKEND_SCHEMA §S5).
  One line is appended and flushed per call, so an interrupted run keeps its accounting.
  Write failures propagate: an eval run must not continue without its accounting.
- :class:`DatabaseSink` — ``llm_call`` rows for live API traffic (BACKEND_SCHEMA §S2). A
  database failure is logged and swallowed so accounting never fails a user's request.
- :class:`MemorySink` — keeps records in a list (tests, CLI).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from receipt_extractor.llm.types import LLMCallRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

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


class DatabaseSink:
    """Inserts each record as an ``llm_call`` row in its own short transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def write(self, record: LLMCallRecord) -> None:
        from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

        from receipt_extractor.db.models import LLMCall  # noqa: PLC0415
        from receipt_extractor.db.repositories import add_llm_calls  # noqa: PLC0415

        row = LLMCall(
            id=record.id,
            created_at=record.created_at,
            run_id=record.run_id,
            request_id=record.request_id,
            case_id=record.case_id,
            stage=record.stage,
            provider=record.provider,
            model=record.model,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            latency_ms=record.latency_ms,
            cache_hit=record.cache_hit,
            retries=record.retries,
            schema_valid=record.schema_valid,
            list_cost_usd=record.list_cost_usd,
            error=record.error,
        )
        try:
            async with self._session_factory() as session:
                await add_llm_calls(session, [row])
                await session.commit()
        except (SQLAlchemyError, OSError) as exc:
            # Type only: driver messages can include connection details.
            logger.error("llm_call row not stored (%s); call id %s", type(exc).__name__, record.id)
