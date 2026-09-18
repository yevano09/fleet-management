"""Telemetry reshape for enterprise scale (P-ret-1).

- Drops the String-UUID primary key (36B/row, unusable at 45k rows/s).
  `id` stays populated for backward-compat reads but is no longer the PK,
  which also makes the table Timescale-hypertable-ready (P-ret-2).
- Adds tenant_id + region (hot-path tenancy stamped at ingest; backfilled
  from devices.org_id where joinable).
- Adds UNIQUE(device_id, timestamp, source): dedup guarantee to replace the
  in-memory LRU at scale.
- Adds composite (tenant_id, device_id, timestamp DESC) for the fleet's
  dominant read pattern.
- Postgres: dtc_codes/tire_pressures Text -> JSONB. SQLite keeps Text.

SQLite path recreates the table via batch mode (small legacy DBs only).

Revision ID: 0002_telemetry_reshape
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_telemetry_reshape"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_pg():
        op.execute("ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS tenant_id VARCHAR")
        op.execute("ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS region VARCHAR DEFAULT 'default'")
        op.execute(
            "UPDATE telemetry SET tenant_id = devices.org_id FROM devices "
            "WHERE devices.id = telemetry.device_id AND telemetry.tenant_id IS NULL"
        )
        op.execute("ALTER TABLE telemetry DROP CONSTRAINT IF EXISTS telemetry_pkey")
        op.execute(
            "ALTER TABLE telemetry ADD CONSTRAINT telemetry_device_ts_source_unique "
            "UNIQUE (device_id, timestamp, source)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_telemetry_tenant_device_ts "
            "ON telemetry (tenant_id, device_id, timestamp DESC)"
        )
        op.execute("ALTER TABLE telemetry ALTER COLUMN dtc_codes TYPE JSONB USING dtc_codes::jsonb")
        op.execute("ALTER TABLE telemetry ALTER COLUMN tire_pressures TYPE JSONB USING tire_pressures::jsonb")
    else:
        # SQLite: no named PK constraint exists to drop; the recreate below
        # copies the table sans PK declaration issues. UNIQUE + composite
        # index are what the hot path needs; PK removal is PG-only (the only
        # hypertable target).
        with op.batch_alter_table("telemetry", recreate="always") as batch:
            batch.add_column(sa.Column("tenant_id", sa.String(), nullable=True))
            batch.add_column(sa.Column("region", sa.String(), nullable=True, server_default="default"))
            batch.create_unique_constraint(
                "telemetry_device_ts_source_unique", ["device_id", "timestamp", "source"]
            )
            batch.create_index(
                "ix_telemetry_tenant_device_ts",
                ["tenant_id", "device_id", "timestamp"],
            )


def downgrade() -> None:
    if _is_pg():
        op.execute("ALTER TABLE telemetry ALTER COLUMN dtc_codes TYPE TEXT USING dtc_codes::text")
        op.execute("ALTER TABLE telemetry ALTER COLUMN tire_pressures TYPE TEXT USING tire_pressures::text")
        op.execute("DROP INDEX IF EXISTS ix_telemetry_tenant_device_ts")
        op.execute("ALTER TABLE telemetry DROP CONSTRAINT IF EXISTS telemetry_device_ts_source_unique")
        op.execute("ALTER TABLE telemetry ADD PRIMARY KEY (id)")
        op.execute("ALTER TABLE telemetry DROP COLUMN IF EXISTS region")
        op.execute("ALTER TABLE telemetry DROP COLUMN IF EXISTS tenant_id")
    else:
        with op.batch_alter_table("telemetry", recreate="always") as batch:
            batch.drop_index("ix_telemetry_tenant_device_ts")
            batch.drop_constraint("telemetry_device_ts_source_unique", type_="unique")
            batch.create_primary_key("telemetry_pkey", ["id"])
            batch.drop_column("region")
            batch.drop_column("tenant_id")
