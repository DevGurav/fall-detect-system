"""create a least-privilege app role so RLS actually enforces

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-03

RLS (migration 0002) is bypassed for superusers and `BYPASSRLS` roles — and the
Postgres image's default role (the one that owns the tables) is a superuser. So
the gateway must connect as a **non-superuser** role for the policies to take
effect. This creates `fall_app` (NOSUPERUSER, CRUD only) and grants it the tables;
the app's DSN uses `fall_app`, while migrations keep running as the owner.

The dev password here mirrors docker-compose's local credentials; in production
the role + secret are provisioned by infrastructure, not committed.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "fall_app"
_DEV_PASSWORD = "fall_app"  # local dev only; prod provisions the role out-of-band


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_ROLE}') THEN
                CREATE ROLE {_ROLE} LOGIN PASSWORD '{_DEV_PASSWORD}'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {_ROLE}")
