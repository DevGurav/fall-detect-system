"""Phase 29 — refresh tokens, calibration write-path, audit service, contacts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import create_device_token, create_user_token
from app.config import get_settings
from app.main import create_app
from app.services.audit_service import AuditService
from app.services.refresh_token_service import (
    InvalidRefreshTokenError,
    RefreshTokenService,
)


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def user_headers() -> dict[str, str]:
    token, _ = create_user_token(get_settings(), uuid4())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def device_headers():
    """Factory: Bearer device token whose `did` matches the given device_id."""
    def _make(device_id: str = "dev-001") -> dict[str, str]:
        token = create_device_token(
            get_settings(), device_pk=uuid4(), device_id=device_id, user_id=uuid4()
        )
        return {"Authorization": f"Bearer {token}"}
    return _make


# ─── RefreshTokenService unit tests (DB-less) ────────────────────────────────

def test_refresh_create_returns_none_without_db():
    svc = RefreshTokenService(get_settings(), db=None)
    result = asyncio.run(svc.create(uuid4()))
    assert result is None


def test_refresh_rotate_raises_without_db():
    svc = RefreshTokenService(get_settings(), db=None)
    with pytest.raises(InvalidRefreshTokenError):
        asyncio.run(svc.rotate("any-token"))


def test_refresh_revoke_all_is_noop_without_db():
    svc = RefreshTokenService(get_settings(), db=None)
    asyncio.run(svc.revoke_all(uuid4()))  # must not raise


def test_refresh_service_is_stub_without_db():
    svc = RefreshTokenService(get_settings(), db=None)
    assert svc.is_stub is True


# ─── POST /v1/auth/refresh ────────────────────────────────────────────────────

def test_refresh_endpoint_503_without_db(client):
    """DB-less mode: require_db gate fires before the service is called."""
    resp = client.post("/v1/auth/refresh", json={"refresh_token": "any"})
    assert resp.status_code == 503


def test_refresh_endpoint_requires_json(client):
    resp = client.post("/v1/auth/refresh", data="not-json")
    assert resp.status_code in (422, 503)


def test_refresh_endpoint_401_on_invalid_token(client):
    mock_svc = MagicMock()
    mock_svc.rotate = AsyncMock(side_effect=InvalidRefreshTokenError("bad token"))
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    client.app.state.db = mock_db
    client.app.state.refresh_token_service = mock_svc

    resp = client.post("/v1/auth/refresh", json={"refresh_token": "expired-or-stolen"})
    assert resp.status_code == 401


def test_refresh_endpoint_returns_new_token_pair(client):
    new_uid = uuid4()
    new_refresh = "new-refresh-token-xyz"

    mock_svc = MagicMock()
    mock_svc.rotate = AsyncMock(return_value=(new_uid, new_refresh))
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    client.app.state.db = mock_db
    client.app.state.refresh_token_service = mock_svc

    resp = client.post("/v1/auth/refresh", json={"refresh_token": "valid-old-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["refresh_token"] == new_refresh
    assert body["expires_in"] > 0


def test_refresh_endpoint_rotates_exactly_once(client):
    """Ensure the old token is passed to rotate (not a copy or re-hash)."""
    old_token = "raw-32-byte-hex-token-from-login"
    mock_svc = MagicMock()
    mock_svc.rotate = AsyncMock(return_value=(uuid4(), "new-token-abc"))
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    client.app.state.db = mock_db
    client.app.state.refresh_token_service = mock_svc

    client.post("/v1/auth/refresh", json={"refresh_token": old_token})
    mock_svc.rotate.assert_awaited_once_with(old_token)


# ─── AuditService unit tests ──────────────────────────────────────────────────

def test_audit_noop_without_db():
    svc = AuditService(db=None)
    asyncio.run(svc.log("test.action", user_id=uuid4()))  # must not raise


def test_audit_swallows_db_exception():
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(side_effect=RuntimeError("DB gone"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db.sessionmaker = MagicMock(return_value=mock_session)

    svc = AuditService(db=mock_db)
    asyncio.run(svc.log("should.not.raise"))  # exception must be swallowed, not propagated


# ─── POST /v1/devices/{id}/calibration-windows ───────────────────────────────

def _dummy_window() -> dict:
    return {
        "device_id": "dev-001",
        "ts_start_unix_ms": 0,
        "sample_rate_hz": 50,
        "samples": [{"ax": 0.0, "ay": 0.0, "az": 9.8, "wx": 0.0, "wy": 0.0, "wz": 0.0}] * 125,
    }


def test_calibration_windows_requires_device_auth(client):
    resp = client.post("/v1/devices/dev-001/calibration-windows", json={"windows": [_dummy_window()]})
    assert resp.status_code == 401


def test_calibration_windows_403_device_id_mismatch(client, device_headers):
    headers = device_headers("dev-001")
    resp = client.post(
        "/v1/devices/other-device/calibration-windows",
        json={"windows": [_dummy_window()]},
        headers=headers,
    )
    assert resp.status_code == 403


def test_calibration_windows_503_without_db(client, device_headers):
    headers = device_headers("dev-001")
    resp = client.post(
        "/v1/devices/dev-001/calibration-windows",
        json={"windows": [_dummy_window()]},
        headers=headers,
    )
    assert resp.status_code == 503


def test_calibration_windows_204_with_mocked_store(client, device_headers):
    mock_store = MagicMock()
    mock_store.accumulate_windows = AsyncMock(return_value=5)
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    client.app.state.db = mock_db
    client.app.state.calibration_store = mock_store

    headers = device_headers("dev-001")
    resp = client.post(
        "/v1/devices/dev-001/calibration-windows",
        json={"windows": [_dummy_window()]},
        headers=headers,
    )
    assert resp.status_code == 204
    mock_store.accumulate_windows.assert_awaited_once()


def test_calibration_windows_rejects_empty_list(client, device_headers):
    headers = device_headers("dev-001")
    resp = client.post(
        "/v1/devices/dev-001/calibration-windows",
        json={"windows": []},
        headers=headers,
    )
    assert resp.status_code == 422


# ─── POST /v1/devices/{id}/calibrate ─────────────────────────────────────────

def test_calibrate_requires_user_auth(client):
    resp = client.post("/v1/devices/dev-001/calibrate")
    assert resp.status_code == 401


def test_calibrate_503_without_db(client, user_headers):
    resp = client.post("/v1/devices/dev-001/calibrate", headers=user_headers)
    assert resp.status_code == 503


def test_calibrate_404_device_not_found(client, user_headers):
    mock_device_svc = MagicMock()
    mock_device_svc.get_device_pk = AsyncMock(return_value=None)
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    client.app.state.db = mock_db
    client.app.state.device_service = mock_device_svc

    resp = client.post("/v1/devices/dev-001/calibrate", headers=user_headers)
    assert resp.status_code == 404


def test_calibrate_returns_calibration_response(client, user_headers):
    device_pk = uuid4()
    fitted_at = datetime.now(tz=timezone.utc)

    mock_device_svc = MagicMock()
    mock_device_svc.get_device_pk = AsyncMock(return_value=device_pk)
    mock_cal_store = MagicMock()
    mock_cal_store.fit = AsyncMock(return_value=(42, fitted_at))
    mock_audit = MagicMock()
    mock_audit.log = AsyncMock()
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()

    client.app.state.db = mock_db
    client.app.state.device_service = mock_device_svc
    client.app.state.calibration_store = mock_cal_store
    client.app.state.audit_service = mock_audit

    resp = client.post("/v1/devices/dev-001/calibrate", headers=user_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "dev-001"
    assert body["n_adl_windows"] == 42
    assert "fitted_at" in body


def test_calibrate_writes_audit_event(client, user_headers):
    device_pk = uuid4()
    fitted_at = datetime.now(tz=timezone.utc)

    mock_device_svc = MagicMock()
    mock_device_svc.get_device_pk = AsyncMock(return_value=device_pk)
    mock_cal_store = MagicMock()
    mock_cal_store.fit = AsyncMock(return_value=(10, fitted_at))
    mock_audit = MagicMock()
    mock_audit.log = AsyncMock()
    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()

    client.app.state.db = mock_db
    client.app.state.device_service = mock_device_svc
    client.app.state.calibration_store = mock_cal_store
    client.app.state.audit_service = mock_audit

    client.post("/v1/devices/dev-001/calibrate", headers=user_headers)
    mock_audit.log.assert_awaited_once()
    call_args = mock_audit.log.call_args
    assert call_args.args[0] == "device.calibrate"


# ─── GET/POST/DELETE /v1/contacts ────────────────────────────────────────────

def test_contacts_list_requires_auth(client):
    assert client.get("/v1/contacts").status_code == 401


def test_contacts_create_requires_auth(client):
    assert client.post("/v1/contacts", json={"name": "X", "phone": "123", "priority": 1}).status_code == 401


def test_contacts_delete_requires_auth(client):
    assert client.delete(f"/v1/contacts/{uuid4()}").status_code == 401


def test_contacts_list_503_without_db(client, user_headers):
    assert client.get("/v1/contacts", headers=user_headers).status_code == 503


def test_contacts_create_503_without_db(client, user_headers):
    resp = client.post(
        "/v1/contacts",
        json={"name": "Priya", "phone": "+919876543210", "priority": 1},
        headers=user_headers,
    )
    assert resp.status_code == 503


def test_contacts_delete_503_without_db(client, user_headers):
    assert client.delete(f"/v1/contacts/{uuid4()}", headers=user_headers).status_code == 503


def test_contacts_create_validates_phone_required(client, user_headers):
    resp = client.post(
        "/v1/contacts",
        json={"name": "Priya", "priority": 1},
        headers=user_headers,
    )
    assert resp.status_code == 422
