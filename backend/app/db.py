"""Async Postgres access (SQLAlchemy 2.0 + asyncpg), gated on settings.

`Database.from_settings()` returns a `Database` only when a DSN is configured;
otherwise it returns `None` and the gateway runs DB-less — the persistence layers
fall back to stub mode, exactly like `CloudDetector` does without a model file.
The instance is built once on `app.state` in the lifespan and disposed on
shutdown (see `app/main.py`).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Database | None:
        url = settings.resolved_database_url
        if not url:
            return None  # DB-less mode
        return cls(create_async_engine(url, pool_pre_ping=True))

    async def dispose(self) -> None:
        await self.engine.dispose()
