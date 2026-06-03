"""Offline schema guards — assert the ORM metadata matches the locked v3 schema.

No database required: these check `Base.metadata` only, so they run in CI without
Postgres and catch a model/migration drift early (the migration in
alembic/versions/0001 must create exactly these tables/columns).
"""
from __future__ import annotations

from app.models import Base

EXPECTED_TABLES = {
    "users",
    "emergency_contacts",
    "devices",
    "pairing_codes",
    "events",
    "retraining_samples",
    "device_calibration",
    "audit_events",
}


def test_schema_has_exactly_the_expected_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_retraining_samples_carries_window_and_owner_scoping():
    cols = set(Base.metadata.tables["retraining_samples"].columns.keys())
    assert {"device_ref", "device_id", "user_id", "window", "label", "ts_start_unix_ms"} <= cols


def test_device_calibration_holds_norm_vectors_and_threshold():
    cols = set(Base.metadata.tables["device_calibration"].columns.keys())
    assert {"channel_mean", "channel_std", "feature_mean", "feature_std", "threshold_override"} <= cols
