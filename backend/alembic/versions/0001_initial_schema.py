"""initial schema: identity, ingestion, personalization, audit

Revision ID: 0001
Revises:
Create Date: 2026-06-03

Creates the eight v3 tables (ARCHITECTURE §2.2). Row-level security (§5) is
deferred to the auth slice — enforcing it now would lock out the trusted-stub
access path — so this migration is purely structural.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255)),
        sa.Column("full_name", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "emergency_contacts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(
        op.f("ix_emergency_contacts_user_id"), "emergency_contacts", ["user_id"]
    )

    op.create_table(
        "devices",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("paired_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("battery_pct", sa.Integer),
        sa.Column("signal_dbm", sa.Integer),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("edge_model_version", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(op.f("ix_devices_device_id"), "devices", ["device_id"], unique=True)
    op.create_index(op.f("ix_devices_user_id"), "devices", ["user_id"])

    op.create_table(
        "pairing_codes",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column(
            "user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(op.f("ix_pairing_codes_code"), "pairing_codes", ["code"], unique=True)
    op.create_index(op.f("ix_pairing_codes_user_id"), "pairing_codes", ["user_id"])

    op.create_table(
        "events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("device_ref", sa.String(128), nullable=False),
        sa.Column("device_id", _UUID, sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("ts_start_unix_ms", sa.BigInteger, nullable=False),
        sa.Column("is_fall", sa.Boolean, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("lead_time_ms", sa.Float),
        sa.Column("peak_ms2", sa.Float),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acked_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(op.f("ix_events_device_ref"), "events", ["device_ref"])
    op.create_index(op.f("ix_events_user_id"), "events", ["user_id"])

    op.create_table(
        "retraining_samples",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("device_ref", sa.String(128), nullable=False),
        sa.Column("device_id", _UUID, sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("ts_start_unix_ms", sa.BigInteger, nullable=False),
        sa.Column("sample_rate_hz", sa.Integer, nullable=False),
        sa.Column("window", postgresql.JSONB, nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("edge_p_pre_impact", sa.Float),
        sa.Column("edge_model_version", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(
        op.f("ix_retraining_samples_device_ref"), "retraining_samples", ["device_ref"]
    )
    op.create_index(
        op.f("ix_retraining_samples_user_id"), "retraining_samples", ["user_id"]
    )

    op.create_table(
        "device_calibration",
        sa.Column(
            "device_id",
            _UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channel_mean", postgresql.ARRAY(sa.Float)),
        sa.Column("channel_std", postgresql.ARRAY(sa.Float)),
        sa.Column("feature_mean", postgresql.ARRAY(sa.Float)),
        sa.Column("feature_std", postgresql.ARRAY(sa.Float)),
        sa.Column("threshold_override", sa.Float),
        sa.Column("n_adl_windows", sa.Integer, nullable=False),
        sa.Column("fitted_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("device_ref", sa.String(128)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(op.f("ix_audit_events_user_id"), "audit_events", ["user_id"])
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("device_calibration")
    op.drop_table("retraining_samples")
    op.drop_table("events")
    op.drop_table("pairing_codes")
    op.drop_table("devices")
    op.drop_table("emergency_contacts")
    op.drop_table("users")
