from sqlalchemy import text, inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings, DEFAULT_ORG_ID


def _engine_kwargs() -> dict:
    """Per-dialect engine tuning (P0 UC-27).

    - Postgres: real connection pool with pre-ping (never NullPool in production).
    - SQLite: aiosqlite defaults are fine for the local/demo profile.
    """
    if settings.database_url.startswith("postgresql"):
        return {
            "echo": False,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
        }
    return {"echo": False}


engine = create_async_engine(settings.database_url, **_engine_kwargs())
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


# Tables that carry a tenant-scoping org_id column (UC-26).
# Child tables (telemetry, ota_deployments, command_queue, shadows…) inherit
# tenancy via their device_id — intentionally NOT listed here.
_TENANT_TABLES = (
    "devices",
    "user_sessions",
    "firmware",
    "geofences",
    "webhook_subscriptions",
    "ota_schedules",
    "alerts",
)


async def _bootstrap_tenancy() -> None:
    """Seed the default organization and backfill org_id on legacy rows.

    - create_all creates org_id columns on FRESH databases.
    - For pre-existing SQLite files created before P0, ALTER TABLE adds the
      column, then any NULLs are backfilled to `org-default`.
    """
    async with engine.begin() as conn:
        def _table_columns(sync_conn) -> dict:
            insp = sa_inspect(sync_conn)
            existing = set(insp.get_table_names())
            return {
                t: {c["name"] for c in insp.get_columns(t)}
                for t in _TENANT_TABLES
                if t in existing
            }

        cols_by_table = await conn.run_sync(_table_columns)

        for table, cols in cols_by_table.items():
            if "org_id" not in cols:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN org_id VARCHAR "
                    f"DEFAULT '{DEFAULT_ORG_ID}' NOT NULL"
                ))
            else:
                await conn.execute(text(
                    f"UPDATE {table} SET org_id = '{DEFAULT_ORG_ID}' WHERE org_id IS NULL"
                ))


async def _seed_default_org() -> None:
    from app.models import Organization

    async with async_session_factory() as db:
        existing = await db.execute(
            text("SELECT id FROM organizations WHERE id = :id"),
            {"id": DEFAULT_ORG_ID},
        )
        if existing.scalar_one_or_none() is None:
            db.add(Organization(id=DEFAULT_ORG_ID, name="Default", slug="default"))
            await db.commit()


async def init_db():
    from app.models import (
        Device, Firmware, OtaDeployment, V2gSchedule, Alert, UserSession,
        Telemetry, Geofence, GeofenceEvent, CommandQueue, AuditLog,
        DeviceShadow, OtaSchedule, PredictedFailure, WebhookSubscription, EventLog,
        Organization, ApiKey, DeviceCertificate,
    )
    from app.aegis.models import Remediation, RuleConfig  # noqa: F401
    _ = (Device, Firmware, OtaDeployment, V2gSchedule, Alert, UserSession,
         Telemetry, Geofence, GeofenceEvent, CommandQueue, AuditLog,
         DeviceShadow, OtaSchedule, PredictedFailure, WebhookSubscription, EventLog,
         Organization, ApiKey, DeviceCertificate)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _bootstrap_tenancy()
    await _seed_default_org()
