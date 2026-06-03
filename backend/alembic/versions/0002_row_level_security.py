"""enable row-level security on the user-scoped tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-03

Scopes every row to its owner with RLS (ARCHITECTURE §5). Policies read the
`app.user_id` GUC the app sets per transaction (app/db.py::session_for); an unset
GUC matches nothing, so a query without an identity returns no rows. FORCE makes
the table owner (the role the app connects as) subject to the policies too — a
dedicated low-privilege role is the documented next hardening step.

`users` and `pairing_codes` are intentionally left policy-free: login looks a user
up by email, and pairing-code redemption looks a code up, *before* any user
context exists. They are never exposed through a list endpoint.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables with a `user_id` column — scoped directly to the GUC.
_USER_SCOPED = ("events", "retraining_samples", "devices", "emergency_contacts", "audit_events")
_GUC = "current_setting('app.user_id', true)::uuid"


def upgrade() -> None:
    for table in _USER_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_user_isolation ON {table} "
            f"USING (user_id = {_GUC}) WITH CHECK (user_id = {_GUC})"
        )
    # device_calibration has no user_id; scope it through its owning device.
    op.execute("ALTER TABLE device_calibration ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_calibration FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY device_calibration_user_isolation ON device_calibration "
        f"USING (EXISTS (SELECT 1 FROM devices d WHERE d.id = device_calibration.device_id "
        f"AND d.user_id = {_GUC})) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM devices d WHERE d.id = device_calibration.device_id "
        f"AND d.user_id = {_GUC}))"
    )


def downgrade() -> None:
    for table in (*_USER_SCOPED, "device_calibration"):
        op.execute(f"DROP POLICY IF EXISTS {table}_user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
