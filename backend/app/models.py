"""SQLAlchemy 2.0 models — the Postgres system of record (ARCHITECTURE §2.2, §5).

One declarative `Base`; the Alembic migrations under `alembic/versions/` are the
source of truth for the live schema (this module and the migration are kept in
lock-step). Eight tables:

  identity     users · emergency_contacts · devices · pairing_codes
  ingestion    events · retraining_samples
  personalize  device_calibration
  compliance   audit_events

**Transitional identity.** Real per-user OAuth + per-device JWT/pairing is a
later slice. Until a device is paired, the ingestion-path tables (`events`,
`retraining_samples`) keep the raw `device_ref` string from the §8 envelope and
allow NULL `device_id` / `user_id`; identity is back-filled when pairing lands.
Row-level security (scoping rows to `user_id`) is likewise deferred to the auth
slice — enforcing it now would lock out the trusted-stub access path.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The single label we collect today (mirrors services/retraining_store.py).
CANCELED_FALSE_ALARM = "CANCELED_FALSE_ALARM"


class Base(DeclarativeBase):
    pass


# ─── Identity ────────────────────────────────────────────────────────────────


class User(Base):
    """A caregiver / account holder."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255))  # set in the auth slice
    full_name: Mapped[str | None] = mapped_column(String(200))
    fcm_token: Mapped[str | None] = mapped_column(String(256))  # caregiver's phone push token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmergencyContact(Base):
    """A contact escalated to when an alert goes unacknowledged (§2.4)."""

    __tablename__ = "emergency_contacts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=1)  # 1 = primary
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    """A paired wearable + its live status (battery / signal / last-seen)."""

    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    # The hardware-agnostic §8 `device_id` string (real ESP32 or virtual device).
    device_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    signal_dbm: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # online | offline | unknown
    edge_model_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PairingCode(Base):
    """An 8-char Crockford-base32 pairing code, 5-min TTL, attempt-limited (§5)."""

    __tablename__ = "pairing_codes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─── Ingestion ───────────────────────────────────────────────────────────────


class Event(Base):
    """A confirmed/cleared fall verdict (the persisted output of /v1/inference).

    Modeled here; the inference path is wired to write it in the telemetry slice.
    """

    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    device_ref: Mapped[str] = mapped_column(String(128), index=True)  # raw §8 device_id
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    ts_start_unix_ms: Mapped[int] = mapped_column(BigInteger)
    is_fall: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16))
    lead_time_ms: Mapped[float | None] = mapped_column(Float)
    peak_ms2: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetrainingSample(Base):
    """A user-canceled false alarm stored for fine-tuning / per-user thresholds.

    The /v1/retraining target (ADR-011). Skips detection by design — it is labeled
    training data, never scored.
    """

    __tablename__ = "retraining_samples"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    device_ref: Mapped[str] = mapped_column(String(128), index=True)
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    ts_start_unix_ms: Mapped[int] = mapped_column(BigInteger)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, default=50)
    window: Mapped[list] = mapped_column(JSONB)  # the 125 IMU samples, as posted
    label: Mapped[str] = mapped_column(String(32), default=CANCELED_FALSE_ALARM)
    edge_p_pre_impact: Mapped[float | None] = mapped_column(Float)
    edge_model_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─── Personalization ─────────────────────────────────────────────────────────


class DeviceCalibration(Base):
    """Per-device personalization profile (ARCHITECTURE §4.6, §3.2).

    `channel_*` / `feature_*` are the per-user z-score normalisers fit on ~10–15
    min of ADL wear at pairing time; `threshold_override` is the per-user decision
    threshold tuned from that user's canceled false alarms. The detector reads
    these per request and falls back to the model's global stats when absent.
    """

    __tablename__ = "device_calibration"

    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    channel_mean: Mapped[list[float] | None] = mapped_column(ARRAY(Float))  # len 6
    channel_std: Mapped[list[float] | None] = mapped_column(ARRAY(Float))  # len 6
    feature_mean: Mapped[list[float] | None] = mapped_column(ARRAY(Float))  # len 43
    feature_std: Mapped[list[float] | None] = mapped_column(ARRAY(Float))  # len 43
    threshold_override: Mapped[float | None] = mapped_column(Float)
    n_adl_windows: Mapped[int] = mapped_column(Integer, default=0)
    fitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ─── Auth tokens ─────────────────────────────────────────────────────────────


class RefreshToken(Base):
    """30-day rotate-on-use refresh token (only the SHA-256 hash is stored)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─── Compliance ──────────────────────────────────────────────────────────────


class AuditEvent(Base):
    """Append-only audit log: every pair / ack / token use (§5)."""

    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    device_ref: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
