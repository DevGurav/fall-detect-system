"""Pydantic v2 schemas for the cloud gateway.

These mirror the single hardware-agnostic ingestion contract in
docs/ARCHITECTURE.md §8 — the exact JSON both the real ESP32-S3 firmware and the
Python virtual device POST to /v1/inference. Strict validation here is a
deliberate fix for v1/v2, which accepted unvalidated input with silent
`.get('x', 0)` defaults.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

WINDOW_SAMPLES = 125  # 2.5 s @ 50 Hz — the locked window length


class IMUSample(BaseModel):
    """One IMU reading: 3-axis accel (m/s²) + 3-axis gyro (rad/s)."""

    ax: float
    ay: float
    az: float
    wx: float
    wy: float
    wz: float


class EdgePrediction(BaseModel):
    """The edge model's trigger, included only when the edge fired."""

    p_pre_impact: float = Field(ge=0.0, le=1.0)
    model_version: str


class PayloadType(str, Enum):
    """Why the watch uploaded this window — picks the cloud routing.

    `emergency`        → a live edge trigger; goes to the CloudDetector for
                         secondary verification (and, later, caregiver alerting).
    `retraining_data`  → a window the user CANCELED during the local grace period
                         (false alarm); skips detection and is stored as labeled
                         training data for future fine-tuning / threshold tuning.
    """

    emergency = "emergency"
    retraining_data = "retraining_data"


class WindowEnvelope(BaseModel):
    """The locked §8 ingestion contract: one 2.5 s IMU window + its edge trigger.

    Shared by every endpoint that ingests a window, so the 125-sample contract is
    validated in exactly one place regardless of how the window is routed.
    """

    device_id: str = Field(min_length=1)
    ts_start_unix_ms: int = Field(ge=0)
    sample_rate_hz: int = 50
    samples: list[IMUSample]
    edge_prediction: EdgePrediction | None = None

    @field_validator("samples")
    @classmethod
    def _exactly_one_window(cls, v: list[IMUSample]) -> list[IMUSample]:
        if len(v) != WINDOW_SAMPLES:
            raise ValueError(
                f"expected exactly {WINDOW_SAMPLES} samples (2.5 s @ 50 Hz), got {len(v)}"
            )
        return v


class InferenceRequest(WindowEnvelope):
    """A 2.5 s window streamed to the cloud when the edge model fires (Payload A).

    `payload_type` defaults to `emergency` so the locked §8 contract and existing
    clients (ESP32 firmware, virtual device) keep working without sending it.
    """

    payload_type: PayloadType = PayloadType.emergency


class RetrainingRequest(WindowEnvelope):
    """A user-canceled false alarm uploaded for MLOps (Payload B).

    `payload_type` is pinned to `retraining_data`: posting an `emergency` window to
    the retraining endpoint is a 422, so a live trigger can't be silently diverted
    into the data-collection path. Clients may omit the field (the URL implies it).
    """

    payload_type: Literal[PayloadType.retraining_data] = PayloadType.retraining_data


class Severity(str, Enum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class InferenceResponse(BaseModel):
    """The cloud detector's verdict on a window."""

    is_fall: bool
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Severity
    action: str                       # e.g. "alert_caregiver" | "suppress"
    lead_time_ms: float | None = None
    peak_ms2: float | None = None     # raw peak acceleration magnitude (m/s²)
    model_version: str


class RetrainingAck(BaseModel):
    """Ack for a stored retraining sample. No detection verdict — by design."""

    stored: bool
    label: str                        # e.g. "CANCELED_FALSE_ALARM"
    sample_id: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    model_version: str
    environment: str


class ReadinessCheck(BaseModel):
    """One dependency's result in the readiness probe (Phase 32)."""

    name: str                                          # "database" | "redis" | "model"
    status: Literal["ok", "skipped", "error"]          # skipped = optional infra not configured
    detail: str | None = None                          # error message when status == "error"


class ReadinessResponse(BaseModel):
    """GET /health/ready — aggregate of the per-dependency checks.

    `status` is "ready" only when no configured dependency reports an error;
    the endpoint returns HTTP 503 alongside a "degraded" body so a probe (or a
    human) can see exactly which check failed.
    """

    status: Literal["ready", "degraded"]
    version: str
    checks: list[ReadinessCheck]


# ─── Telemetry: device heartbeat + read-side views (W2) ──────────────────────


class HeartbeatRequest(BaseModel):
    """A periodic device status ping (ARCHITECTURE §2.1 — watch sends ~every 5 min)."""

    device_id: str = Field(min_length=1)
    battery_pct: int | None = Field(default=None, ge=0, le=100)
    signal_dbm: int | None = None
    edge_model_version: str | None = None


class DeviceOut(BaseModel):
    """A device's live status. `status` is derived from `last_seen_at` at read time."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_id: str
    status: str                       # online | offline | unknown (derived)
    battery_pct: int | None
    signal_dbm: int | None
    last_seen_at: datetime | None
    paired_at: datetime | None
    edge_model_version: str | None
    created_at: datetime


class EventOut(BaseModel):
    """A persisted fall verdict, as returned on the caregiver timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_ref: str
    ts_start_unix_ms: int
    is_fall: bool
    confidence: float
    severity: Severity
    lead_time_ms: float | None
    model_version: str
    acknowledged_at: datetime | None
    acked_by: UUID | None
    created_at: datetime


class EventPage(BaseModel):
    """A page of the fall-event timeline (newest first)."""

    items: list[EventOut]
    total: int
    limit: int
    offset: int


# ─── Auth + pairing (Week D security perimeter) ──────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int                   # seconds
    refresh_token: str | None = None  # 30-day opaque token; None in DB-less/stub mode


class PairingCodeResponse(BaseModel):
    """A short-lived code a paired user shows to a device during provisioning."""

    code: str
    expires_at: datetime


class PairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=8)   # 8-char Crockford base32
    device_id: str = Field(min_length=1)


class PairResponse(BaseModel):
    """The device's long-lived token, returned once on successful pairing."""

    device_token: str
    token_type: str = "bearer"
    device_id: str
    user_id: UUID


# ─── Push notifications + manual emergency (Phase 28b) ───────────────────────


class PushTokenRequest(BaseModel):
    """The caregiver app's FCM registration token (refreshed on each login)."""

    token: str = Field(min_length=1)


class EmergencyRequest(BaseModel):
    """A manually triggered SOS alert from the caregiver app."""

    device_ref: str | None = None   # optional: which device the patient wears
    note: str | None = Field(default=None, max_length=500)


class EmergencyResponse(BaseModel):
    event_id: UUID | None       # None when DB-less
    created_at: datetime


# ─── Refresh token (Phase 29) ─────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# ─── Calibration (Phase 29 — fit-at-first personalization) ───────────────────


class CalibrationWindowsRequest(BaseModel):
    """A batch of ADL windows sent during the 15-min fit-at-first session.

    Same WindowEnvelope contract (125 samples @ 50 Hz) but routed to the
    calibration accumulator instead of the fall detector.
    """

    windows: list[WindowEnvelope] = Field(min_length=1)


class CalibrationResponse(BaseModel):
    """Returned by POST /v1/devices/{id}/calibrate after fitting."""

    device_id: str
    n_adl_windows: int
    fitted_at: datetime


# ─── Emergency contacts (Phase 29) ───────────────────────────────────────────


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=32)
    priority: int = Field(default=1, ge=1, le=10)


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone: str
    priority: int
    created_at: datetime
