"""User auth — register, login, and refresh — /v1/auth/*.

register + login both return a short-lived access token (15 min, HS256 JWT) plus
a long-lived refresh token (30-day opaque hex, rotate-on-use).  POST /refresh
accepts the refresh token and returns a fresh pair — so the caregiver app never
has to re-enter credentials.  Passwords are bcrypt-hashed by the UserService.
All three endpoints require a database and are rate-limited.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import create_user_token
from app.deps import require_db
from app.ratelimit import rate_limit
from app.schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.services.refresh_token_service import InvalidRefreshTokenError
from app.services.user_service import EmailTakenError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


async def _token_response(request: Request, user_id: UUID) -> TokenResponse:
    """Mint access + refresh tokens and return a TokenResponse."""
    token, ttl = create_user_token(request.app.state.settings, user_id)
    refresh = await request.app.state.refresh_token_service.create(user_id)
    return TokenResponse(access_token=token, expires_in=ttl, refresh_token=refresh)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth", 10, 60))],
)
async def register(req: RegisterRequest, request: Request) -> TokenResponse:
    require_db(request)
    try:
        user = await request.app.state.user_service.register(
            req.email, req.password, req.full_name
        )
    except EmailTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None
    resp = await _token_response(request, user.id)
    await request.app.state.audit_service.log(
        "user.register", user_id=user.id, details={"email": req.email}
    )
    return resp


@router.post(
    "/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("auth", 10, 60))]
)
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    require_db(request)
    user = await request.app.state.user_service.authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    resp = await _token_response(request, user.id)
    await request.app.state.audit_service.log(
        "user.login", user_id=user.id, details={"email": req.email}
    )
    return resp


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth", 10, 60))],
)
async def refresh(req: RefreshRequest, request: Request) -> TokenResponse:
    """Rotate a refresh token and return a fresh access + refresh pair.

    The old refresh token is revoked on first use — a stolen token self-invalidates
    when the legitimate client uses it. Returns 401 on any invalid/expired token.
    """
    require_db(request)
    try:
        user_id, new_refresh = await request.app.state.refresh_token_service.rotate(
            req.refresh_token
        )
    except InvalidRefreshTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired refresh token") from None
    token, ttl = create_user_token(request.app.state.settings, user_id)
    return TokenResponse(access_token=token, expires_in=ttl, refresh_token=new_refresh)
