"""Engine/session factory construction (no database needed: engines connect lazily)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from receipt_extractor.config import load_settings
from receipt_extractor.db.session import (
    DatabaseURLError,
    create_engine,
    create_engine_from_settings,
    create_session_factory,
)

PASSWORD = "db-password-sentinel"  # noqa: S105 - sentinel, not a credential


async def test_engine_pool_is_sized_from_settings(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", f"postgresql+asyncpg://u:{PASSWORD}@localhost:5432/db")
    clean_env.setenv("DB_POOL_SIZE", "7")
    clean_env.setenv("DB_MAX_OVERFLOW", "3")
    clean_env.setenv("DB_POOL_TIMEOUT_S", "2.5")
    engine = create_engine_from_settings(load_settings())
    try:
        pool = engine.pool
        assert pool.size() == 7  # type: ignore[attr-defined]
        assert pool._max_overflow == 3  # type: ignore[attr-defined]
        assert pool._timeout == 2.5  # type: ignore[attr-defined]
        assert PASSWORD not in repr(engine)
    finally:
        await engine.dispose()


async def test_default_pool_size_is_five() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@localhost/db")
    try:
        assert engine.pool.size() == 5  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://u:{PASSWORD}@localhost/db",  # sync driver
        f"sqlite+aiosqlite:///{PASSWORD}.db",
    ],
)
def test_rejects_non_asyncpg_url_without_echoing_it(url: str) -> None:
    with pytest.raises(DatabaseURLError, match="postgresql\\+asyncpg") as excinfo:
        create_engine(url)
    assert PASSWORD not in str(excinfo.value)


def test_rejects_unparseable_url_without_echoing_it() -> None:
    with pytest.raises(DatabaseURLError) as excinfo:
        create_engine(f"not a url {PASSWORD}")
    assert PASSWORD not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


async def test_session_factory_settings() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@localhost/db")
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            assert isinstance(session, AsyncSession)
            assert session.sync_session.expire_on_commit is False
    finally:
        await engine.dispose()
