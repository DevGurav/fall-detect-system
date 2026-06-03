"""Shared test fixtures: Bearer auth headers signed with the app's own secret.

`create_app()` reads `get_settings()` at startup, so signing test tokens with
`get_settings()` here uses the identical `jwt_secret` the app verifies against.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.auth import create_device_token, create_user_token
from app.config import get_settings


@pytest.fixture
def device_headers():
    """Factory: a Bearer device token whose `did` matches the given device_id."""

    def _make(device_id: str = "dev-001") -> dict[str, str]:
        token = create_device_token(
            get_settings(), device_pk=uuid4(), device_id=device_id, user_id=uuid4()
        )
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def user_headers() -> dict[str, str]:
    token, _ = create_user_token(get_settings(), uuid4())
    return {"Authorization": f"Bearer {token}"}
