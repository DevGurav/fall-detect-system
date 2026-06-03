"""End-to-end smoke test for the stateful gateway against a real database.

Drives the full telemetry pipeline over HTTP — Heartbeat -> Inference (fall) ->
GET Events -> Acknowledge — and asserts each step actually persisted. Run it
against a gateway wired to Postgres (see ../docker-compose.yml + README.md):

    docker compose up -d --wait                     # from the repo root
    cd backend
    $env:FG_DATABASE_URL = "postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian"
    $env:FG_MODEL_PATH   = "<a path that does not exist>"   # stub detector -> deterministic fall
    uv run uvicorn app.main:app
    uv run python scripts/integration_smoke.py

The stub detector is used only to make the fall verdict deterministic; the event
write path is identical for the real ONNX model. Exits non-zero on the first
failed assertion.
"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
DEVICE_ID = f"smoke-watch-{int(time.time())}"


def _fall_window(spike_index: int = 60, spike_az: float = 35.0) -> list[dict]:
    """125 resting samples (~1 g) with one impact spike — a clean fall for the stub."""
    rest = {"ax": 0.0, "ay": 0.0, "az": 9.81, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    samples = [dict(rest) for _ in range(125)]
    samples[spike_index] = {"ax": 0.0, "ay": 0.0, "az": spike_az, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    return samples


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        # 1. Heartbeat — registers the device and records battery / signal / last-seen.
        dev = c.post(
            "/v1/devices/heartbeat",
            json={"device_id": DEVICE_ID, "battery_pct": 88, "signal_dbm": -57},
        ).raise_for_status().json()
        assert dev["status"] == "online", dev
        assert dev["battery_pct"] == 88, dev
        print(f"[1] heartbeat         -> device {dev['id']} status={dev['status']} battery={dev['battery_pct']}%")

        # 2. The device appears in the live-status list, online.
        listed = [d for d in c.get("/v1/devices").raise_for_status().json() if d["device_id"] == DEVICE_ID]
        assert len(listed) == 1 and listed[0]["status"] == "online", listed
        print(f"[2] GET /v1/devices   -> 1 matching device, status={listed[0]['status']}")

        # 3. Inference on a fall window -> confirmed fall -> event persisted.
        verdict = c.post(
            "/v1/inference",
            json={
                "device_id": DEVICE_ID,
                "ts_start_unix_ms": int(time.time() * 1000),
                "sample_rate_hz": 50,
                "samples": _fall_window(),
            },
        ).raise_for_status().json()
        assert verdict["is_fall"] is True, verdict
        print(
            f"[3] POST /v1/inference -> is_fall={verdict['is_fall']} "
            f"severity={verdict['severity']} model={verdict['model_version']}"
        )

        # 4. The event is on the timeline, unacknowledged.
        page = c.get("/v1/events", params={"device_id": DEVICE_ID}).raise_for_status().json()
        assert page["total"] == 1, page
        event = page["items"][0]
        assert event["is_fall"] is True and event["acknowledged_at"] is None, event
        event_id = event["id"]
        print(f"[4] GET /v1/events    -> total={page['total']} event={event_id} ack={event['acknowledged_at']}")

        # 5. Acknowledge the alert.
        acked = c.post(f"/v1/events/{event_id}/acknowledge").raise_for_status().json()
        assert acked["acknowledged_at"] is not None, acked
        print(f"[5] POST acknowledge  -> acknowledged_at={acked['acknowledged_at']}")

        # 6. The acknowledgement persisted (re-read from the DB).
        reread = c.get("/v1/events", params={"device_id": DEVICE_ID}).raise_for_status().json()["items"][0]
        assert reread["acknowledged_at"] is not None, reread
        print(f"[6] GET /v1/events    -> acknowledgement persisted: {reread['acknowledged_at']}")

    print("\nPASS - heartbeat -> inference(fall) -> events -> acknowledge all persisted to Postgres.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, httpx.HTTPError) as exc:
        print(f"\nFAIL - {type(exc).__name__}: {exc}")
        sys.exit(1)
