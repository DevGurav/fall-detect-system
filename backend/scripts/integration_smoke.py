"""End-to-end smoke test for the secured gateway against a real database.

Drives the full AUTHENTICATED pipeline over HTTP — register -> pairing-code ->
pair -> heartbeat -> inference(fall) -> events -> acknowledge — and asserts each
step persisted. Run against a gateway wired to Postgres and connected as the
non-superuser fall_app role (see ../docker-compose.yml + README.md):

    docker compose up -d --wait                              # from the repo root
    cd backend
    $env:FG_DATABASE_URL = "postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian"
    uv run alembic upgrade head                              # schema + RLS + the fall_app role
    $env:FG_DATABASE_URL = "postgresql+asyncpg://fall_app:fall_app@localhost:5432/fall_guardian"
    $env:FG_MODEL_PATH   = "<a path that does not exist>"    # stub detector -> deterministic fall
    uv run uvicorn app.main:app
    uv run python scripts/integration_smoke.py

The stub detector just makes the fall verdict deterministic; the persisted path is
identical for the real ONNX model. Exits non-zero on the first failed assertion.
"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
STAMP = int(time.time())
EMAIL = f"smoke-{STAMP}@example.com"
DEVICE_ID = f"smoke-watch-{STAMP}"


def _fall_window(spike_index: int = 60, spike_az: float = 35.0) -> list[dict]:
    rest = {"ax": 0.0, "ay": 0.0, "az": 9.81, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    samples = [dict(rest) for _ in range(125)]
    samples[spike_index] = {"ax": 0.0, "ay": 0.0, "az": spike_az, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    return samples


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        # 1. Register a caregiver -> per-user access token.
        token = (
            c.post("/v1/auth/register", json={"email": EMAIL, "password": "password123"})
            .raise_for_status()
            .json()["access_token"]
        )
        user = {"Authorization": f"Bearer {token}"}
        print("[1] register      -> user token")

        # 2. Mint a pairing code (user auth).
        code = c.post("/v1/devices/pairing-codes", headers=user).raise_for_status().json()["code"]
        print(f"[2] pairing-code  -> {code}")

        # 3. Device redeems the code -> per-device token.
        pair = (
            c.post("/v1/devices/pair", json={"code": code, "device_id": DEVICE_ID})
            .raise_for_status()
            .json()
        )
        device = {"Authorization": f"Bearer {pair['device_token']}"}
        print(f"[3] pair          -> device token (owner {pair['user_id']})")

        # 4. Heartbeat (device auth).
        hb = (
            c.post(
                "/v1/devices/heartbeat", headers=device,
                json={"device_id": DEVICE_ID, "battery_pct": 88},
            )
            .raise_for_status()
            .json()
        )
        assert hb["status"] == "online", hb
        print(f"[4] heartbeat     -> status={hb['status']} battery={hb['battery_pct']}%")

        # 5. Inference on a fall window (device auth) -> confirmed fall -> event.
        verdict = (
            c.post(
                "/v1/inference", headers=device,
                json={
                    "device_id": DEVICE_ID,
                    "ts_start_unix_ms": int(time.time() * 1000),
                    "sample_rate_hz": 50,
                    "samples": _fall_window(),
                },
            )
            .raise_for_status()
            .json()
        )
        assert verdict["is_fall"] is True, verdict
        print(f"[5] inference     -> is_fall={verdict['is_fall']} severity={verdict['severity']}")

        # 6. The event is on the caller's timeline.
        page = (
            c.get("/v1/events", headers=user, params={"device_id": DEVICE_ID})
            .raise_for_status()
            .json()
        )
        assert page["total"] == 1, page
        event_id = page["items"][0]["id"]
        print(f"[6] events        -> total={page['total']} event={event_id}")

        # 7. Acknowledge (user auth).
        acked = (
            c.post(f"/v1/events/{event_id}/acknowledge", headers=user).raise_for_status().json()
        )
        assert acked["acknowledged_at"] is not None, acked
        print(f"[7] acknowledge   -> acknowledged_at={acked['acknowledged_at']}")

        # 8. Unauthenticated access is rejected.
        assert c.get("/v1/events").status_code == 401, "events must require a token"
        print("[8] no-token /v1/events -> 401")

    print("\nPASS - register -> pair -> heartbeat -> inference(fall) -> events -> ack, authenticated + persisted.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, httpx.HTTPError) as exc:
        print(f"\nFAIL - {type(exc).__name__}: {exc}")
        sys.exit(1)
