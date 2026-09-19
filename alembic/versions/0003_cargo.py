"""Smart Cargo tables (SRS Idea 4, M1.1).

Creates cargo_profiles, cargo_readings, shock_events. Fresh databases get
them via create_all; this revision covers pre-existing databases.

Revision ID: 0003_cargo
Revises: 0002_telemetry_reshape
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_cargo"
down_revision = "0002_telemetry_reshape"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cargo_profiles",
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("commodity", sa.String(), default="general"),
        sa.Column("temp_min_c", sa.Float(), default=2.0),
        sa.Column("temp_max_c", sa.Float(), default=4.0),
        sa.Column("thermal_mass", sa.Float(), default=1.0),
        sa.Column("door_alerts", sa.Boolean(), default=True),
        sa.Column("trip_eta_minutes", sa.Float(), nullable=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organizations.id"), default="org-default"),
    )
    op.create_index("ix_cargo_profiles_device_id", "cargo_profiles", ["device_id"])
    op.create_table(
        "cargo_readings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), index=True),
        sa.Column("bay_temp_c", sa.Float(), nullable=True),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("door_open", sa.Boolean(), default=False),
        sa.Column("shock_g", sa.Float(), nullable=True),
        sa.Column("source", sa.String(), default="sim"),
        sa.Column("ai_inference", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(), default="org-default", index=True),
    )
    op.create_index("ix_cargo_device_ts", "cargo_readings", ["device_id", "timestamp"])
    op.create_table(
        "shock_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(), index=True),
        sa.Column("peak_g", sa.Float(), nullable=False),
        sa.Column("axis", sa.String(), default="z"),
        sa.Column("event_class", sa.String(), nullable=False, index=True),
        sa.Column("model_version", sa.String(), default="heuristic-v1"),
        sa.Column("org_id", sa.String(), default="org-default", index=True),
    )


def downgrade() -> None:
    op.drop_table("shock_events")
    op.drop_table("cargo_readings")
    op.drop_table("cargo_profiles")
