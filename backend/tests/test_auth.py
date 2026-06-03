"""Auth + pairing — unit tests (crypto/JWT/codes) and DB-less gating contracts.

The full register → pairing-code → pair → authenticated-call flow needs Postgres
and is verified live (see backend/README.md / BUILD_LOG). Here we cover the pure
primitives and that every protected route rejects a missing or wrong-type token.
"""
from __future__ import annotations

from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import (
    PAIRING_CODE_LEN,
    create_device_token,
    create_user_token,
    generate_pairing_code,
    hash_password,
    verify_password,
)
from app.config import get_settings
from app.main import create_app

_CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
WINDOW = 125


@pytest.fixture
def client():
    with TestClient(create_app()) as c:  # DB-less
        yield c


def _request(device_id: str = "dev-001"):
    samples = [{"ax": 0.0, "ay": 0.0, "az": 9.81, "wx": 0.0, "wy": 0.0, "wz": 0.0} for _ in range(WINDOW)]
    return {"device_id": device_id, "ts_start_unix_ms": 0, "sample_rate_hz": 50, "samples": samples}


# ─── primitives ──────────────────────────────────────────────────────────────


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong password", h)


def test_pairing_code_is_8_char_crockford():
    code = generate_pairing_code()
    assert len(code) == PAIRING_CODE_LEN == 8
    assert set(code) <= _CROCKFORD  # no ambiguous I/L/O/U


def test_user_token_roundtrip():
    settings = get_settings()
    uid = uuid4()
    token, ttl = create_user_token(settings, uid)
    assert ttl > 0
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == str(uid) and payload["typ"] == "user"


def test_device_token_roundtrip():
    settings = get_settings()
    pk, uid = uuid4(), uuid4()
    token = create_device_token(settings, device_pk=pk, device_id="dev-9", user_id=uid)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["typ"] == "device"
    assert payload["did"] == "dev-9"
    assert payload["uid"] == str(uid)


# ─── gating: protected routes reject missing / wrong-type tokens ─────────────


def test_inference_requires_device_token(client):
    assert client.post("/v1/inference", json=_request()).status_code == 401


def test_inference_rejects_user_token(client, user_headers):
    r = client.post("/v1/inference", json=_request(), headers=user_headers)
    assert r.status_code == 401  # a user token is not a device token


def test_inference_rejects_device_id_mismatch(client, device_headers):
    r = client.post("/v1/inference", json=_request("dev-OTHER"), headers=device_headers("dev-001"))
    assert r.status_code == 403  # token's device may not post as another device


def test_events_require_user_token(client):
    assert client.get("/v1/events").status_code == 401


def test_events_reject_device_token(client, device_headers):
    assert client.get("/v1/events", headers=device_headers()).status_code == 401


def test_register_requires_db(client):
    # Register takes no auth but needs a DB -> 503 DB-less (body is otherwise valid).
    r = client.post("/v1/auth/register", json={"email": "a@b.co", "password": "password123"})
    assert r.status_code == 503
