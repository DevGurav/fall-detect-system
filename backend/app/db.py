"""Async Postgres access (SQLAlchemy 2.0 + asyncpg), gated on settings.

`Database.from_settings()` returns a `Database` only when a DSN is configured;
otherwise it returns `None` and the gateway runs DB-less — the persistence layers
fall back to stub mode, exactly like `CloudDetector` does without a model file.
The instance is built once on `app.state` in the lifespan and disposed on
shutdown (see `app/main.py`).

`session_for(user_id)` opens a session whose transaction carries the `app.user_id`
GUC that the Postgres RLS policies (migration 0002) read, so every row a request
touches is isolated to its owner at the database layer.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
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

    @asynccontextmanager
    async def session_for(self, user_id: UUID | None) -> AsyncIterator[AsyncSession]:
        """A session whose transaction is scoped to `user_id` for Postgres RLS.

        The policies in migration 0002 read `app.user_id`; we set it transaction-
        locally (`set_config(..., is_local=true)`) so it resets on commit/rollback
        and never leaks across pooled connections. An unset GUC matches no rows, so
        a query that forgets to scope returns nothing rather than leaking. Passing
        `user_id=None` sets no GUC — only for paths that touch RLS-free tables.
        """
        async with self.sessionmaker() as session:
            if user_id is not None:
                await session.execute(
                    text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)}
                )
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
