"""Alembic environment — async (SQLAlchemy 2.0 + asyncpg).

The URL is pulled from app settings so there is one source of truth for the DSN
(FG_DATABASE_URL / FG_RETRAINING_DB_DSN). `target_metadata = Base.metadata`, so
`alembic revision --autogenerate` diffs against `app/models.py`.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = get_settings().resolved_database_url
    if not url:
        raise RuntimeError(
            "Set FG_DATABASE_URL (or FG_RETRAINING_DB_DSN) to a "
            "postgresql+asyncpg:// DSN before running migrations."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
