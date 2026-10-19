"""Baseline migration: applies to an empty database, round-trips, and matches the models."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from receipt_extractor.db.models import Base

pytestmark = pytest.mark.integration

SHARED_TABLES = {"eval_run", "eval_result", "llm_call"}
EXPECTED_INDEXES = {
    "eval_result": {"ix_eval_result_run_id_status"},
    "llm_call": {"ix_llm_call_run_id", "ix_llm_call_created_at"},
}


def _snapshot(connection: Connection) -> dict[str, Any]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    return {
        "tables": tables,
        "indexes": {
            table: {index["name"] for index in inspector.get_indexes(table)}
            for table in SHARED_TABLES & tables
        },
        "extensions": set(
            connection.execute(text("SELECT extname FROM pg_extension")).scalars().all()
        ),
        "version": (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if "alembic_version" in tables
            else None
        ),
    }


def _with_connection[T](url: str, fn: Any) -> T:
    async def run() -> T:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                result: T = await connection.run_sync(fn)
                return result
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _assert_head_schema(snapshot: dict[str, Any]) -> None:
    assert snapshot["tables"] >= SHARED_TABLES | {"alembic_version"}
    assert "vector" in snapshot["extensions"]
    assert snapshot["version"] == "0001_baseline"
    for table, indexes in EXPECTED_INDEXES.items():
        assert indexes <= snapshot["indexes"][table]


def test_upgrade_applies_cleanly_to_empty_database(
    empty_database_url: str, make_alembic_config: Callable[[str], Config]
) -> None:
    before: dict[str, Any] = _with_connection(empty_database_url, _snapshot)
    assert not before["tables"] & SHARED_TABLES

    command.upgrade(make_alembic_config(empty_database_url), "head")

    _assert_head_schema(_with_connection(empty_database_url, _snapshot))


def test_downgrade_then_upgrade_round_trips(
    empty_database_url: str, make_alembic_config: Callable[[str], Config]
) -> None:
    config = make_alembic_config(empty_database_url)
    command.upgrade(config, "head")
    at_head: dict[str, Any] = _with_connection(empty_database_url, _snapshot)

    command.downgrade(config, "base")
    at_base: dict[str, Any] = _with_connection(empty_database_url, _snapshot)
    assert not at_base["tables"] & SHARED_TABLES
    assert "vector" not in at_base["extensions"]
    assert at_base["version"] is None

    command.upgrade(config, "head")
    again: dict[str, Any] = _with_connection(empty_database_url, _snapshot)
    _assert_head_schema(again)
    assert again == at_head


def test_migrated_schema_matches_models(migrated_database_url: str) -> None:
    """Autogenerate finds no difference between the migration and the SQLAlchemy models."""

    def diff(connection: Connection) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    assert _with_connection(migrated_database_url, diff) == []
