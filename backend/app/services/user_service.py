"""User accounts — registration + password authentication (ARCHITECTURE §5).

DB-gated like the other services. Tokens are minted by the router (app/auth.py);
this service owns the user rows and bcrypt verification only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from uuid import UUID

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

    async def update_push_token(self, user_id: UUID, token: str) -> None:
        """Store or refresh the caregiver's FCM push token."""
        async with self._db.session_for(user_id) as session:
            user = await session.get(User, user_id)
            if user is not None:
                user.fcm_token = token
                await session.commit()

    async def get_fcm_token(self, user_id: UUID) -> str | None:
        """Return the user's registered FCM token, or None if not set."""
        async with self._db.session_for(user_id) as session:
            user = await session.get(User, user_id)
            return user.fcm_token if user else None

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
