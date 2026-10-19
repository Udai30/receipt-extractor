"""Alembic environment for the async (``asyncpg``) engine.

The database URL comes from ``config.attributes["database_url"]`` when Alembic is driven
programmatically (the testcontainers fixtures do this), otherwise from ``DATABASE_URL`` via
:func:`receipt_extractor.config.get_settings`. It is never read from ``alembic.ini``, so no
credentials live in the repository.

Offline mode (``alembic upgrade head --sql``) needs no database or URL: it renders the
PostgreSQL DDL for review.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from receipt_extractor.db.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

_CONFIGURE_OPTS = {
    "target_metadata": target_metadata,
    "compare_type": True,
    "compare_server_default": True,
    "transaction_per_migration": True,
}


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if isinstance(url, str) and url:
        return url
    from receipt_extractor.config import get_settings  # noqa: PLC0415 - only when online

    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it."""
    context.configure(
        dialect_name="postgresql",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_CONFIGURE_OPTS,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **_CONFIGURE_OPTS)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations on a single, unpooled connection."""
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
