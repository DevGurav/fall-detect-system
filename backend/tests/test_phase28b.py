"""Phase 28b — push-token + emergency endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import create_user_token
from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def user_headers() -> dict[str, str]:
    token, _ = create_user_token(get_settings(), uuid4())
    return {"Authorization": f"Bearer {token}"}


# ─── PUT /v1/users/me/push-token ─────────────────────────────────────────────


def test_push_token_requires_auth(client):
    resp = client.put("/v1/users/me/push-token", json={"token": "abc123"})
    assert resp.status_code == 401


def test_push_token_503_without_db(client, user_headers):
    """DB-less mode → 503 (require_db gate)."""
    resp = client.put(
        "/v1/users/me/push-token",
        json={"token": "fcm-registration-token-xyz"},
        headers=user_headers,
    )
    assert resp.status_code == 503


def test_push_token_rejects_empty_token(client, user_headers):
    resp = client.put("/v1/users/me/push-token", json={"token": ""}, headers=user_headers)
    assert resp.status_code == 422


def test_push_token_success_with_stubbed_service(client, user_headers):
    mock_service = MagicMock()
    mock_service.update_push_token = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    client.app.state.db = mock_db  # non-None → passes require_db, has dispose()
    client.app.state.user_service = mock_service

    resp = client.put(
        "/v1/users/me/push-token",
        json={"token": "fcm-token-abc123"},
        headers=user_headers,
    )
    assert resp.status_code == 204
    mock_service.update_push_token.assert_awaited_once()
    _, token_arg = mock_service.update_push_token.call_args.args
    assert token_arg == "fcm-token-abc123"


# ─── POST /v1/emergency ──────────────────────────────────────────────────────


def test_emergency_requires_auth(client):
    resp = client.post("/v1/emergency", json={})
    assert resp.status_code == 401


def test_emergency_stub_mode_no_db(client, user_headers):
    """DB-less: record_sos returns (None, now) — 201 with null event_id."""
    resp = client.post("/v1/emergency", json={}, headers=user_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["event_id"] is None
    assert "created_at" in body


def test_emergency_with_device_ref_and_note(client, user_headers):
    resp = client.post(
        "/v1/emergency",
        json={"device_ref": "watch-001", "note": "Patient fell in bathroom"},
        headers=user_headers,
    )
    assert resp.status_code == 201


def test_emergency_note_too_long(client, user_headers):
    resp = client.post(
        "/v1/emergency",
        json={"note": "x" * 501},
        headers=user_headers,
    )
    assert resp.status_code == 422


def test_emergency_returns_event_id_when_store_present(client, user_headers):
    fake_event_id = uuid4()
    fake_now = datetime.now(tz=timezone.utc)

    mock_store = MagicMock()
    mock_store.record_sos = AsyncMock(return_value=(fake_event_id, fake_now))
    client.app.state.event_store = mock_store

    resp = client.post("/v1/emergency", json={}, headers=user_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["event_id"] == str(fake_event_id)

    kwargs = mock_store.record_sos.call_args.kwargs
    assert kwargs["device_ref"] == "manual-sos"
    assert kwargs["note"] is None


def test_emergency_passes_device_ref_to_store(client, user_headers):
    mock_store = MagicMock()
    mock_store.record_sos = AsyncMock(return_value=(uuid4(), datetime.now(tz=timezone.utc)))
    client.app.state.event_store = mock_store

    client.post(
        "/v1/emergency",
        json={"device_ref": "esp32-001"},
        headers=user_headers,
    )
    kwargs = mock_store.record_sos.call_args.kwargs
    assert kwargs["device_ref"] == "esp32-001"


# ─── FcmService stub behaviour ───────────────────────────────────────────────


def test_fcm_service_is_stub_without_credentials():
    from app.services.fcm_service import FcmService

    assert FcmService(None).is_stub is True


def test_fcm_service_is_stub_with_invalid_json():
    from app.services.fcm_service import FcmService

    assert FcmService("not-valid-json{{{").is_stub is True
