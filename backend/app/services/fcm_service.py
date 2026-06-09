"""Firebase Cloud Messaging — push a fall alert to a caregiver's phone.

Gated on FG_FIREBASE_CREDENTIALS (the service-account JSON string).  When unset
the service is a no-op: SSE still fires, just no push to a terminated app.

Uses the FCM HTTP v1 API:
  POST https://fcm.googleapis.com/v1/projects/{project}/messages:send
  Authorization: Bearer {google_oauth2_access_token}

Token refresh is done synchronously via google-auth then handed to httpx for the
actual HTTP call — the refresh is cached by the Credentials object (~1 hr TTL)
so it doesn't block on every fall.
"""
from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


class FcmService:
    def __init__(self, credentials_json: str | None) -> None:
        self._credentials = None
        self._project_id: str | None = None

        if credentials_json:
            try:
                from google.oauth2 import service_account

                info = json.loads(credentials_json)
                self._project_id = info.get("project_id")
                self._credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=[_FCM_SCOPE]
                )
                logger.info("FCM service initialised for project %s", self._project_id)
            except Exception as exc:
                logger.warning("FCM init failed — push notifications disabled: %s", exc)
                self._credentials = None

    @property
    def is_stub(self) -> bool:
        return self._credentials is None

    async def _get_token(self) -> str:
        """Refresh the OAuth2 access token in a thread (google-auth is sync)."""
        from google.auth.transport.requests import Request

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._credentials.refresh, Request()))
        return self._credentials.token

    async def send_fall_notification(
        self,
        *,
        fcm_token: str,
        event_id: UUID | None,
        device_id: str,
        severity: str,
        confidence: float,
    ) -> None:
        """Push a confirmed-fall alert to the caregiver's device."""
        if self.is_stub or not fcm_token:
            return
        await self._send(
            fcm_token=fcm_token,
            title="Fall Detected",
            body=f"Severity: {severity.capitalize()} — {device_id}",
            data={
                "type": "fall",
                "event_id": str(event_id) if event_id else "",
                "device_id": device_id,
                "severity": severity,
                "confidence": f"{confidence:.2f}",
            },
        )

    async def send_sos_notification(self, *, fcm_token: str, triggered_by: str) -> None:
        """Push a manual SOS alert."""
        if self.is_stub or not fcm_token:
            return
        await self._send(
            fcm_token=fcm_token,
            title="Emergency SOS",
            body=f"Manual SOS triggered by {triggered_by}",
            data={"type": "sos"},
        )

    async def _send(
        self, *, fcm_token: str, title: str, body: str, data: dict[str, str]
    ) -> None:
        import httpx

        try:
            access_token = await self._get_token()
            url = _FCM_URL.format(project_id=self._project_id)
            payload = {
                "message": {
                    "token": fcm_token,
                    "notification": {"title": title, "body": body},
                    "data": data,
                    "android": {"priority": "high"},
                    "apns": {
                        "headers": {"apns-priority": "10"},
                        "payload": {"aps": {"sound": "default"}},
                    },
                }
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code != 200:
                    logger.warning("FCM send failed: %s %s", resp.status_code, resp.text)
                else:
                    logger.debug("FCM sent to token …%s", fcm_token[-6:])
        except Exception as exc:
            # Push failures must never break the main fall-alert path.
            logger.warning("FCM send exception (non-fatal): %s", exc)
