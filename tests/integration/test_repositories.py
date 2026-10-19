"""Repository insert/select round-trips and schema constraints for the shared tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from receipt_extractor.db import repositories as repo
from receipt_extractor.db.models import EvalResult, EvalRun, LLMCall

pytestmark = pytest.mark.integration


def make_run(**overrides: Any) -> EvalRun:
    fields: dict[str, Any] = {
        "run_key": f"key-{uuid.uuid4().hex}",
        "name": "hybrid-rrf-k10-oss20b",
        "variant": "ocr-llm",
        "config": {"pipeline": "ocr-llm", "params": {"k": 10}},
        "dataset_split": "test",
        "dataset_hash": "sha256:abc",
        "sample_size": 100,
        "status": "completed",
        "metrics": {"field_f1": 0.91, "ci95": [0.88, 0.93]},
    }
    fields.update(overrides)
    return EvalRun(**fields)


def make_result(run_id: uuid.UUID, case_id: str, status: str = "pass") -> EvalResult:
    return EvalResult(
        run_id=run_id,
        case_id=case_id,
        status=status,
        score=0.5,
        prediction={"items": []},
        gold={"items": [{"name": "Kopi"}]},
    )


def make_call(run_id: uuid.UUID | None, **overrides: Any) -> LLMCall:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "latency_ms": 420,
        "cache_hit": False,
    }
    fields.update(overrides)
    return LLMCall(**fields)


# --- eval_run -------------------------------------------------------------------------


async def test_eval_run_round_trip(db_session: AsyncSession) -> None:
    started = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
    run = await repo.add_eval_run(
        db_session,
        make_run(
            latency_p50_ms=800,
            latency_p95_ms=2100,
            tokens_in=5_000_000_000,  # needs bigint
            tokens_out=12,
            list_cost_usd=Decimal("12.345678"),
            started_at=started,
        ),
    )
    assert isinstance(run.id, uuid.UUID)
    assert run.created_at.tzinfo is not None

    db_session.expunge_all()  # force a real SELECT
    loaded = await repo.get_eval_run(db_session, run.id)
    assert loaded is not None
    assert loaded is not run
    assert loaded.config == {"pipeline": "ocr-llm", "params": {"k": 10}}
    assert loaded.metrics["ci95"] == [0.88, 0.93]
    assert loaded.tokens_in == 5_000_000_000
    assert loaded.list_cost_usd == Decimal("12.345678")
    assert loaded.started_at == started
    assert loaded.finished_at is None

    by_key = await repo.get_eval_run_by_key(db_session, run.run_key)
    assert by_key is not None
    assert by_key.id == run.id


async def test_eval_run_missing_returns_none(db_session: AsyncSession) -> None:
    assert await repo.get_eval_run(db_session, uuid.uuid4()) is None
    assert await repo.get_eval_run_by_key(db_session, "nope") is None


async def test_list_eval_runs_filters_and_bounds(db_session: AsyncSession) -> None:
    await repo.add_eval_run(db_session, make_run(variant="donut"))
    await repo.add_eval_run(db_session, make_run(variant="gemini"))
    await repo.add_eval_run(db_session, make_run(variant="gemini"))

    assert {r.variant for r in await repo.list_eval_runs(db_session)} == {"donut", "gemini"}
    gemini = await repo.list_eval_runs(db_session, variant="gemini")
    assert len(gemini) == 2
    assert len(await repo.list_eval_runs(db_session, limit=1)) == 1
    for bad in (0, repo.MAX_LIMIT + 1):
        with pytest.raises(ValueError, match="limit"):
            await repo.list_eval_runs(db_session, limit=bad)


async def test_eval_run_key_is_unique(db_session: AsyncSession) -> None:
    await repo.add_eval_run(db_session, make_run(run_key="same"))
    with pytest.raises(IntegrityError, match="uq_eval_run_run_key"):
        async with db_session.begin_nested():
            await repo.add_eval_run(db_session, make_run(run_key="same"))


async def test_eval_run_status_is_checked(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_eval_run_status"):
        async with db_session.begin_nested():
            await repo.add_eval_run(db_session, make_run(status="running"))


# --- eval_result ----------------------------------------------------------------------


async def test_eval_result_round_trip(db_session: AsyncSession) -> None:
    run = await repo.add_eval_run(db_session, make_run())
    inserted = await repo.add_eval_results(
        db_session,
        [
            make_result(run.id, "case-002", "fail"),
            make_result(run.id, "case-001"),
            make_result(run.id, "case-003", "error"),
        ],
    )
    assert all(isinstance(r.id, uuid.UUID) for r in inserted)

    db_session.expunge_all()
    results = await repo.list_eval_results(db_session, run.id)
    assert [r.case_id for r in results] == ["case-001", "case-002", "case-003"]
    first = results[0]
    assert first.details == {}  # server default
    assert first.gold == {"items": [{"name": "Kopi"}]}
    assert first.failure_label is None

    failed = await repo.list_eval_results(db_session, run.id, status="fail")
    assert [r.case_id for r in failed] == ["case-002"]
    assert await repo.list_eval_results(db_session, uuid.uuid4()) == []
    with pytest.raises(ValueError, match="status"):
        await repo.list_eval_results(db_session, run.id, status="bogus")


async def test_eval_result_case_is_unique_per_run(db_session: AsyncSession) -> None:
    run = await repo.add_eval_run(db_session, make_run())
    other = await repo.add_eval_run(db_session, make_run())
    await repo.add_eval_results(
        db_session, [make_result(run.id, "c1"), make_result(other.id, "c1")]
    )
    with pytest.raises(IntegrityError, match="uq_eval_result_run_id_case_id"):
        async with db_session.begin_nested():
            await repo.add_eval_results(db_session, [make_result(run.id, "c1")])


async def test_eval_result_requires_existing_run(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="fk_eval_result_run_id_eval_run"):
        async with db_session.begin_nested():
            await repo.add_eval_results(db_session, [make_result(uuid.uuid4(), "c1")])


async def test_eval_result_status_is_checked(db_session: AsyncSession) -> None:
    run = await repo.add_eval_run(db_session, make_run())
    with pytest.raises(IntegrityError, match="ck_eval_result_status"):
        async with db_session.begin_nested():
            await repo.add_eval_results(db_session, [make_result(run.id, "c1", "skipped")])


async def test_deleting_run_cascades_to_its_results_only(db_session: AsyncSession) -> None:
    run = await repo.add_eval_run(db_session, make_run())
    keep = await repo.add_eval_run(db_session, make_run())
    await repo.add_eval_results(db_session, [make_result(run.id, "a"), make_result(keep.id, "b")])

    await db_session.delete(run)
    await db_session.flush()
    db_session.expunge_all()

    assert await repo.list_eval_results(db_session, run.id) == []
    assert len(await repo.list_eval_results(db_session, keep.id)) == 1


# --- llm_call -------------------------------------------------------------------------


async def test_llm_call_round_trip(db_session: AsyncSession) -> None:
    run = await repo.add_eval_run(db_session, make_run())
    await repo.add_llm_calls(
        db_session,
        [
            make_call(
                run.id,
                case_id="case-001",
                stage="structure",
                tokens_in=900,
                tokens_out=150,
                schema_valid=True,
                list_cost_usd=Decimal("0.00012345"),
            ),
            make_call(None, request_id="req-1", error="timeout", cache_hit=True, retries=2),
        ],
    )

    db_session.expunge_all()
    [eval_call] = await repo.list_llm_calls(db_session, run_id=run.id)
    assert eval_call.case_id == "case-001"
    assert eval_call.retries == 0  # server default
    assert eval_call.list_cost_usd == Decimal("0.00012345")
    assert eval_call.schema_valid is True

    [live_call] = await repo.list_llm_calls(db_session, run_id=None)
    assert live_call.run_id is None
    assert live_call.request_id == "req-1"
    assert (live_call.cache_hit, live_call.retries, live_call.error) == (True, 2, "timeout")
    assert live_call.schema_valid is None


async def test_run_with_llm_calls_cannot_be_deleted(db_session: AsyncSession) -> None:
    """llm_call does not cascade (BACKEND_SCHEMA §S1), so accounting data is never lost."""
    run = await repo.add_eval_run(db_session, make_run())
    await repo.add_llm_calls(db_session, [make_call(run.id)])
    with pytest.raises(IntegrityError, match="fk_llm_call_run_id_eval_run"):
        async with db_session.begin_nested():
            await db_session.delete(run)
            await db_session.flush()


async def test_caller_commit_is_isolated_by_fixture(db_session: AsyncSession) -> None:
    """Repositories never commit; a caller's commit stays inside the test transaction."""
    await repo.add_eval_run(db_session, make_run(run_key="committed-in-test"))
    await db_session.commit()
    found = await db_session.scalar(select(EvalRun).where(EvalRun.run_key == "committed-in-test"))
    assert found is not None
