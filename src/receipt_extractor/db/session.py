"""Async engine and session factory (TRD §S1: SQLAlchemy 2, ``asyncpg``).

Nothing here connects at import time. The application builds one engine per process
(usually at startup) with :func:`create_engine_from_settings` and disposes it on shutdown.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from receipt_extractor.config import Settings

APPLICATION_NAME: Final = "receipt_extractor"


class DatabaseURLError(ValueError):
    """Raised when ``DATABASE_URL`` is not an async PostgreSQL URL."""


def create_engine(
    url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_timeout_s: float = 10.0,
) -> AsyncEngine:
    """Create a pooled async engine for ``url`` (must use the ``postgresql+asyncpg`` driver).

    ``pool_pre_ping`` discards connections the server closed (e.g. Neon's idle suspend),
    and ``pool_timeout_s`` bounds how long a request waits for a free connection instead of
    queueing indefinitely under load.
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise DatabaseURLError("DATABASE_URL is not a valid SQLAlchemy URL.") from None
    if parsed.drivername != "postgresql+asyncpg":
        # Report only the driver: the URL itself contains the password.
        raise DatabaseURLError(
            f"DATABASE_URL must use the 'postgresql+asyncpg' driver, got '{parsed.drivername}'."
        )
    return create_async_engine(
        parsed,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_s,
        pool_pre_ping=True,
        connect_args={"server_settings": {"application_name": APPLICATION_NAME}},
    )


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    """Create the process engine from validated :class:`Settings`."""
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout_s=settings.db_pool_timeout_s,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to ``engine``.

    ``expire_on_commit=False`` keeps loaded attributes usable after commit; under asyncio an
    expired attribute would otherwise trigger implicit I/O and raise ``MissingGreenlet``.
    """
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
