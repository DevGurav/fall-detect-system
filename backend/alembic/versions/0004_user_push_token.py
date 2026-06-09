"""add fcm_token to users for caregiver push notifications

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-09

The FCM token belongs to the *caregiver's phone* (Flutter app), not the
patient's wearable.  When a fall is confirmed the backend fans out a push to
the device-owner's registered token so the alert fires even when the app is
killed.  One token per user (last-registered wins — fine for the single-device
caregiver case; a proper multi-device fan-out table is a post-v3 concern).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("fcm_token", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "fcm_token")
