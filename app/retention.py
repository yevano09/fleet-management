"""P-ret-1 retention worker: 24h-hot raw, 5-min warm rollups, tiered expiry.

Layout (all tiers): raw rows live on hot disk for HOT_HOURS (default 24h),
then age into 5-minute rollups (warm, queryable, cheap); raw AND rollups past
RETENTION_DAYS (default 7, per-tenant in P-ret-3) are dropped. Retention
applies forward only — upgrades never resurrect expired data.

Idempotent by construction: rollup upserts keyed (device_id, bucket), so
overlapping windows and restarts only rewrite identical rows. A watermark is
kept in memory (overlap on restart is safe, just rework).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models import Telemetry, TelemetryRollup5m
from app.utils import utcnow

logger = logging.getLogger(__name__)

ROLLUP_MINUTES = 5

_last_rollup_upto = None


def _is_pg() -> bool:
    return settings.database_url.startswith("postgresql")


def _bucket_expr():
    if _is_pg():
        return text("to_timestamp(floor(extract(epoch from timestamp)/300)*300)")
    return text("datetime(CAST(strftime('%s', timestamp)/300 AS INTEGER)*300, 'unixepoch')")


async def _rollup_window(db: AsyncSession, start, end) -> int:
    """Aggregate raw rows in [start, end) into telemetry_5m. Returns row count."""
    bucket = _bucket_expr()
    agg = (
        select(
            Telemetry.device_id,
            Telemetry.tenant_id,
            bucket.label("bucket"),
            func.count(Telemetry.id).label("samples"),
            func.avg(Telemetry.signal_strength).label("avg_signal"),
            func.min(Telemetry.signal_strength).label("min_signal"),
            func.avg(Telemetry.temperature).label("avg_temp"),
            func.max(Telemetry.temperature).label("max_temp"),
            func.avg(Telemetry.soc).label("avg_soc"),
        )
        .where(Telemetry.timestamp >= start, Telemetry.timestamp < end)
        .group_by(Telemetry.device_id, Telemetry.tenant_id, bucket)
    )
    rows = (await db.execute(agg)).all()
    if not rows:
        return 0
    if _is_pg():
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(TelemetryRollup5m).values([
            {
                "device_id": r.device_id, "tenant_id": r.tenant_id or "org-default",
                "bucket": r.bucket, "samples": r.samples,
                "avg_signal": r.avg_signal, "min_signal": r.min_signal,
                "avg_temp": r.avg_temp, "max_temp": r.max_temp, "avg_soc": r.avg_soc,
            }
            for r in rows
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["device_id", "bucket"],
            set_={
                "samples": stmt.excluded.samples,
                "avg_signal": stmt.excluded.avg_signal,
                "min_signal": stmt.excluded.min_signal,
                "avg_temp": stmt.excluded.avg_temp,
                "max_temp": stmt.excluded.max_temp,
                "avg_soc": stmt.excluded.avg_soc,
            },
        )
    else:
        from sqlalchemy.dialects.sqlite import insert as lite_insert

        stmt = lite_insert(TelemetryRollup5m).values([
            {
                "device_id": r.device_id, "tenant_id": r.tenant_id or "org-default",
                "bucket": r.bucket, "samples": r.samples,
                "avg_signal": r.avg_signal, "min_signal": r.min_signal,
                "avg_temp": r.avg_temp, "max_temp": r.max_temp, "avg_soc": r.avg_soc,
            }
            for r in rows
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["device_id", "bucket"],
            set_={
                "samples": stmt.excluded.samples,
                "avg_signal": stmt.excluded.avg_signal,
                "min_signal": stmt.excluded.min_signal,
                "avg_temp": stmt.excluded.avg_temp,
                "max_temp": stmt.excluded.max_temp,
                "avg_soc": stmt.excluded.avg_soc,
            },
        )
    await db.execute(stmt)
    await db.commit()
    return len(rows)


async def retention_sweep() -> dict:
    """One sweep: roll up newly-cold raw, drop past-retention raw + rollups."""
    from app.metrics import retention_runs_total, telemetry_dropped_total, telemetry_tiered_total

    global _last_rollup_upto
    now = utcnow()
    hot_cutoff = now - timedelta(hours=settings.telemetry_hot_hours)
    retention_cutoff = now - timedelta(days=settings.telemetry_retention_days)
    start = _last_rollup_upto or retention_cutoff
    result = {"rolled_up": 0, "raw_dropped": 0, "rollups_dropped": 0}
    try:
        async with async_session_factory() as db:
            if start < hot_cutoff:
                result["rolled_up"] = await _rollup_window(db, start, hot_cutoff)
                telemetry_tiered_total.inc(result["rolled_up"])
                _last_rollup_upto = hot_cutoff
            raw = await db.execute(
                delete(Telemetry).where(Telemetry.timestamp < retention_cutoff)
            )
            result["raw_dropped"] = raw.rowcount or 0
            telemetry_dropped_total.labels(tier="raw").inc(result["raw_dropped"])
            old = await db.execute(
                delete(TelemetryRollup5m).where(TelemetryRollup5m.bucket < retention_cutoff)
            )
            result["rollups_dropped"] = old.rowcount or 0
            telemetry_dropped_total.labels(tier="rollup").inc(result["rollups_dropped"])
            await db.commit()
        retention_runs_total.labels(result="ok").inc()
    except Exception:
        logger.exception("Retention sweep failed")
        retention_runs_total.labels(result="error").inc()
    return result


async def retention_loop() -> None:
    """Leader-only background loop (started in lifespan).

    Sweeps immediately on boot (catch-up for downtime gaps), then every
    retention_sweep_interval_seconds.
    """
    interval = getattr(settings, "retention_sweep_interval_seconds", 600)
    await retention_sweep()
    while True:
        await asyncio.sleep(interval)
        await retention_sweep()
