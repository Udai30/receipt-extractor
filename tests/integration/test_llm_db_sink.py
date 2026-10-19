"""DatabaseSink writes llm_call rows (ephemeral testcontainers database only)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from receipt_extractor.db.models import LLMCall
from receipt_extractor.db.session import create_engine, create_session_factory
from receipt_extractor.llm.sinks import DatabaseSink
from receipt_extractor.llm.types import LLMCallRecord

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_database_url(
    empty_database_url: str, make_alembic_config: Callable[[str], Config]
) -> str:
    """A migrated database of its own: the sink commits, so it must not share state."""
    command.upgrade(make_alembic_config(empty_database_url), "head")
    return empty_database_url


async def test_database_sink_inserts_llm_call_row(fresh_database_url: str) -> None:
    engine = create_engine(fresh_database_url, pool_size=1, max_overflow=0)
    try:
        sink = DatabaseSink(create_session_factory(engine))
        record = LLMCallRecord(
            request_id="req-1",
            case_id="case-1",
            stage="extract:repair",
            model_alias="groq-oss-20b",
            provider="groq",
            model="groq/openai/gpt-oss-20b",
            tokens_in=100,
            tokens_out=20,
            latency_ms=321,
            cache_hit=False,
            retries=1,
            schema_valid=True,
            list_cost_usd=Decimal("0.00001350"),
        )
        await sink.write(record)

        async with create_session_factory(engine)() as session:
            row = (await session.execute(select(LLMCall))).scalar_one()
        assert row.id == record.id
        assert row.run_id is None
        assert (row.provider, row.model, row.stage) == (
            "groq",
            "groq/openai/gpt-oss-20b",
            "extract:repair",
        )
        assert (row.tokens_in, row.tokens_out, row.latency_ms, row.retries) == (100, 20, 321, 1)
        assert row.list_cost_usd == Decimal("0.00001350")
        assert row.schema_valid is True and row.cache_hit is False
    finally:
        await engine.dispose()


async def test_database_sink_swallows_db_errors(
    fresh_database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    engine = create_engine(fresh_database_url, pool_size=1, max_overflow=0)
    try:
        sink = DatabaseSink(create_session_factory(engine))
        # FK violation: no eval_run with this id exists.
        record = LLMCallRecord(
            run_id=uuid.uuid4(),
            model_alias="a",
            provider="groq",
            model="groq/x",
            latency_ms=1,
            cache_hit=False,
        )
        await sink.write(record)  # logged, not raised: accounting never fails a request
        assert "IntegrityError" in caplog.text and str(record.id) in caplog.text
        assert "postgresql" not in caplog.text  # no connection details in the log
    finally:
        await engine.dispose()
