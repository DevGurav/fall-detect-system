"""Pydantic v2 schemas for the cloud gateway.

These mirror the single hardware-agnostic ingestion contract in
docs/ARCHITECTURE.md §8 — the exact JSON both the real ESP32-S3 firmware and the
Python virtual device POST to /v1/inference. Strict validation here is a
deliberate fix for v1/v2, which accepted unvalidated input with silent
`.get('x', 0)` defaults.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
