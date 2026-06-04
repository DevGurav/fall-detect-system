"""User auth — POST /v1/auth/register + POST /v1/auth/login.

Both return a per-user access token (HS256 JWT). Passwords are bcrypt-hashed by the
UserService. Require a database.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import create_user_token
from app.deps import require_db
from app.ratelimit import rate_limit
from app.schemas import LoginRequest, RegisterRequest, TokenResponse
from app.services.user_service import EmailTakenError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


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
    token, ttl = create_user_token(request.app.state.settings, user.id)
    return TokenResponse(access_token=token, expires_in=ttl)


@router.post(
    "/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("auth", 10, 60))]
)
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    require_db(request)
    user = await request.app.state.user_service.authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token, ttl = create_user_token(request.app.state.settings, user.id)
    return TokenResponse(access_token=token, expires_in=ttl)
