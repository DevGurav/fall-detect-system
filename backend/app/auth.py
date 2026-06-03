"""Authentication + pairing primitives (ARCHITECTURE §2.2 / §5).

Replaces the trusted `X-User-Id` stub with real tokens:

  * **Per-user** access tokens (caregivers) and **per-device** tokens (issued at
    pairing) are HS256 JWTs (PyJWT), distinguished by a `typ` claim.
  * Passwords are bcrypt-hashed.
  * Pairing codes are 8-char Crockford base32 (no ambiguous I/L/O/U).

The FastAPI dependencies verify the `Authorization: Bearer <jwt>` header and yield
the caller's identity (a user id, or a `DeviceIdentity`). Tokens are signed with
`settings.jwt_secret`, which must be overridden outside local dev (see config).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

# Crockford base32 minus the ambiguous I, L, O, U.
_PAIRING_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIRING_CODE_LEN = 8

_bearer = HTTPBearer(auto_error=False)


# ─── passwords ───────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# ─── pairing codes ───────────────────────────────────────────────────────────


def generate_pairing_code() -> str:
    """A cryptographically-random 8-char Crockford-base32 code (~40 bits)."""
    return "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LEN))


# ─── JWT ─────────────────────────────────────────────────────────────────────


def _encode(settings: Settings, claims: dict, ttl: timedelta) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {**claims, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_user_token(settings: Settings, user_id: UUID) -> tuple[str, int]:
    """A per-user access token + its lifetime in seconds."""
    ttl = timedelta(minutes=settings.user_access_ttl_min)
    return _encode(settings, {"sub": str(user_id), "typ": "user"}, ttl), int(ttl.total_seconds())


def create_device_token(settings: Settings, *, device_pk: UUID, device_id: str, user_id: UUID) -> str:
    """A per-device token issued at pairing; carries the owning user + §8 device_id."""
    ttl = timedelta(days=settings.device_token_ttl_days)
    return _encode(
        settings,
        {"sub": str(device_pk), "typ": "device", "did": device_id, "uid": str(user_id)},
        ttl,
    )


def _decode(settings: Settings, token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from None


@dataclass(frozen=True)
class DeviceIdentity:
    device_pk: UUID
    device_id: str
    user_id: UUID


# ─── dependencies (replace the trusted X-User-Id stub) ───────────────────────


async def get_current_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> UUID:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = _decode(request.app.state.settings, creds.credentials)
    if payload.get("typ") != "user":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a user token is required")
    return UUID(payload["sub"])


async def get_current_device(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> DeviceIdentity:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = _decode(request.app.state.settings, creds.credentials)
    if payload.get("typ") != "device":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a device token is required")
    return DeviceIdentity(
        device_pk=UUID(payload["sub"]), device_id=payload["did"], user_id=UUID(payload["uid"])
    )
