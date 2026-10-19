"""Shared pytest fixtures.

Database fixtures run against an **ephemeral testcontainers PostgreSQL** (pgvector image)
that the test session creates and destroys — never against the docker compose database
(CLAUDE.md §2). Tests that use them are skipped with a clear reason when Docker is not
reachable.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Final

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from receipt_extractor.config import Settings, get_settings
from receipt_extractor.db.session import create_engine

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PGVECTOR_IMAGE: Final = "pgvector/pgvector:pg16"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live tests spend free-tier quota and need the network: run them only when selected
    explicitly with ``-m live`` (they still skip individually when a key is missing)."""
    if "live" in (config.getoption("markexpr", "") or ""):
        return
    skip = pytest.mark.skip(reason="live provider test; run with `uv run pytest -m live`")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    """Isolate a test from the developer's real environment and ``.env`` file.

    Removes every variable ``Settings`` reads, runs from an empty temp directory (so no
    ``.env`` is found) and clears the cached settings before and after.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


# --- Database (testcontainers) --------------------------------------------------------


def alembic_config(database_url: str) -> Config:
    """Alembic config for ``database_url``, passed in memory (never via the ini file)."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logging"] = False
    return config


def _require_docker() -> None:
    import docker  # noqa: PLC0415 - only needed by integration tests
    from docker.errors import DockerException  # noqa: PLC0415

    try:
        client = docker.from_env()
        try:
            client.ping()
        finally:
            client.close()
    except DockerException as exc:
        pytest.skip(f"Docker is not reachable ({type(exc).__name__}); start Docker Desktop")


@pytest.fixture(scope="session")
def make_alembic_config() -> Callable[[str], Config]:
    """Factory fixture for :func:`alembic_config` (test modules cannot import conftest)."""
    return alembic_config


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """One throwaway pgvector PostgreSQL for the whole test session."""
    _require_docker()
    with PostgresContainer(PGVECTOR_IMAGE, driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def migrated_database_url(postgres_container: PostgresContainer) -> str:
    """URL of the container's default database with all migrations applied."""
    url = postgres_container.get_connection_url()
    command.upgrade(alembic_config(url), "head")
    return url


async def _admin_execute(admin_url: str, statement: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture
def empty_database_url(postgres_container: PostgresContainer) -> Iterator[str]:
    """A brand-new, empty database inside the container; dropped after the test.

    Sync fixture: Alembic's async env runs its own event loop, so tests that drive Alembic
    must not run inside one.
    """
    admin_url = postgres_container.get_connection_url()
    name = f"test_{uuid.uuid4().hex}"  # generated identifier, safe to interpolate
    asyncio.run(_admin_execute(admin_url, f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(_admin_execute(admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def db_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    """A small pooled engine per test (engines must not cross event loops)."""
    engine = create_engine(migrated_database_url, pool_size=2, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is rolled back after the test.

    ``create_savepoint`` turns the code under test's own commits/rollbacks into savepoint
    operations, so every test starts from the freshly migrated, empty schema.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
