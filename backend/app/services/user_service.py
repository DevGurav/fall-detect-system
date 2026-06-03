"""User accounts — registration + password authentication (ARCHITECTURE §5).

DB-gated like the other services. Tokens are minted by the router (app/auth.py);
this service owns the user rows and bcrypt verification only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import hash_password, verify_password
from app.config import Settings
from app.models import User

if TYPE_CHECKING:
    from app.db import Database


class EmailTakenError(Exception):
    """Raised when registering an email that already exists."""


class UserService:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    async def register(self, email: str, password: str, full_name: str | None) -> User:
        async with self._db.sessionmaker() as session:
            existing = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing is not None:
                raise EmailTakenError(email)
            user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
            session.add(user)
            try:
                await session.commit()
            except IntegrityError as exc:  # unique-email race
                raise EmailTakenError(email) from exc
            await session.refresh(user)
            return user

    async def authenticate(self, email: str, password: str) -> User | None:
        async with self._db.sessionmaker() as session:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
        if user is None or not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user
