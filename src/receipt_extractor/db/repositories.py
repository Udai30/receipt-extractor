"""Repository functions for the shared tables.

Functions take an :class:`AsyncSession` and never commit: the caller owns the unit of work
(``async with session.begin(): ...``). Inserts flush so server-generated ``id`` and
``created_at`` are populated on the returned objects. List queries are always bounded.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt_extractor.db.models import EVAL_RESULT_STATUSES, EvalResult, EvalRun, LLMCall

DEFAULT_LIMIT: Final = 100
MAX_LIMIT: Final = 1000


def _bounded(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
    return limit


# --- eval_run -------------------------------------------------------------------------


async def add_eval_run(session: AsyncSession, run: EvalRun) -> EvalRun:
    """Insert ``run`` and return it with ``id``/``created_at`` populated."""
    session.add(run)
    await session.flush()
    return run


async def get_eval_run(session: AsyncSession, run_id: uuid.UUID) -> EvalRun | None:
    """Return the run with primary key ``run_id``, or ``None``."""
    return await session.get(EvalRun, run_id)


async def get_eval_run_by_key(session: AsyncSession, run_key: str) -> EvalRun | None:
    """Return the run identified by its config/dataset hash ``run_key``, or ``None``."""
    result = await session.execute(select(EvalRun).where(EvalRun.run_key == run_key))
    return result.scalar_one_or_none()


async def list_eval_runs(
    session: AsyncSession, *, variant: str | None = None, limit: int = DEFAULT_LIMIT
) -> Sequence[EvalRun]:
    """Return the newest runs first, optionally filtered by ``variant``."""
    stmt = select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
    if variant is not None:
        stmt = stmt.where(EvalRun.variant == variant)
    result = await session.execute(stmt.limit(_bounded(limit)))
    return result.scalars().all()


# --- eval_result ----------------------------------------------------------------------


async def add_eval_results(
    session: AsyncSession, results: Sequence[EvalResult]
) -> Sequence[EvalResult]:
    """Insert ``results`` in one flush and return them with ids populated."""
    session.add_all(results)
    await session.flush()
    return results


async def list_eval_results(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> Sequence[EvalResult]:
    """Return a run's results ordered by ``case_id``, optionally filtered by ``status``.

    Served by the ``(run_id, status)`` index and the ``(run_id, case_id)`` unique index.
    """
    stmt = select(EvalResult).where(EvalResult.run_id == run_id)
    if status is not None:
        if status not in EVAL_RESULT_STATUSES:
            raise ValueError(f"status must be one of {EVAL_RESULT_STATUSES}, got {status!r}")
        stmt = stmt.where(EvalResult.status == status)
    stmt = stmt.order_by(EvalResult.case_id).limit(_bounded(limit))
    result = await session.execute(stmt)
    return result.scalars().all()


# --- llm_call -------------------------------------------------------------------------


async def add_llm_calls(session: AsyncSession, calls: Sequence[LLMCall]) -> Sequence[LLMCall]:
    """Insert ``calls`` in one flush and return them with ids populated."""
    session.add_all(calls)
    await session.flush()
    return calls


async def list_llm_calls(
    session: AsyncSession, *, run_id: uuid.UUID | None, limit: int = DEFAULT_LIMIT
) -> Sequence[LLMCall]:
    """Return calls of ``run_id`` (or live-traffic calls when ``None``), oldest first."""
    condition = LLMCall.run_id.is_(None) if run_id is None else LLMCall.run_id == run_id
    stmt = (
        select(LLMCall)
        .where(condition)
        .order_by(LLMCall.created_at, LLMCall.id)
        .limit(_bounded(limit))
    )
    result = await session.execute(stmt)
    return result.scalars().all()
