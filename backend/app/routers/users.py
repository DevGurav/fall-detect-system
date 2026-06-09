"""User-account management — /v1/users/me/*.

Currently one endpoint: registering / refreshing the caregiver app's FCM push
token so the backend can send fall alerts to a killed/backgrounded app.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import get_current_user
from app.deps import require_db
from app.schemas import PushTokenRequest

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.put("/me/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def update_push_token(
    req: PushTokenRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
) -> None:
    """Register or refresh the caregiver app's FCM token.

    Called by the Flutter app on every login so the backend always has a
    fresh token.  Overwrites the previous value (last-registered wins).
    """
    require_db(request)
    await request.app.state.user_service.update_push_token(user_id, req.token)
