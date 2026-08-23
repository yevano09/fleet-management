"""
Fleet Commander — Telemetry API (Feature 1)

Time-series telemetry retrieval for trend charts and analytics.
"""

from __future__ import annotations

import logging
from typing import Optional
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.deps import require_user, require_role
from app.models import Telemetry, Device
from app.schemas import TelemetrySeriesResponse, TelemetryPoint
from app.utils import utcnow
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/{device_id}", response_model=TelemetrySeriesResponse)
async def get_telemetry(
    device_id: str,
    hours: int = Query(24, ge=1, le=168, description="Lookback window in hours"),
    limit: int = Query(500, ge=1, le=5000),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """Fetch telemetry time-series for a device."""
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    if not dev_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Device not found")

    cutoff = utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(Telemetry)
        .where(Telemetry.device_id == device_id, Telemetry.timestamp >= cutoff)
        .order_by(Telemetry.timestamp.asc())
        .limit(limit)
    )
    points = result.scalars().all()
    return TelemetrySeriesResponse(
        device_id=device_id,
        points=[TelemetryPoint.model_validate(p) for p in points],
        total=len(points),
    )


@router.get("/{device_id}/latest", response_model=Optional[TelemetryPoint])
async def get_latest_telemetry(
    device_id: str,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the most recent telemetry point for a device."""
    result = await db.execute(
        select(Telemetry)
        .where(Telemetry.device_id == device_id)
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    )
    point = result.scalar_one_or_none()
    if not point:
        raise HTTPException(status_code=404, detail="No telemetry found")
    return TelemetryPoint.model_validate(point)


@router.delete("/{device_id}")
async def prune_telemetry(
    device_id: str,
    days: int = Query(settings.telemetry_retention_days, ge=1),
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Delete telemetry older than N days for a device."""
    cutoff = utcnow() - timedelta(days=days)
    result = await db.execute(
        delete(Telemetry).where(Telemetry.device_id == device_id, Telemetry.timestamp < cutoff)
    )
    await db.commit()
    return {"deleted": result.rowcount, "device_id": device_id, "days": days}


@router.get("/{device_id}/stats")
async def get_telemetry_stats(
    device_id: str,
    hours: int = Query(24, ge=1, le=168),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """Compute summary statistics over the telemetry window."""
    cutoff = utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(
            func.count(Telemetry.id).label("samples"),
            func.avg(Telemetry.signal_strength).label("avg_signal"),
            func.min(Telemetry.signal_strength).label("min_signal"),
            func.max(Telemetry.signal_strength).label("max_signal"),
            func.avg(Telemetry.soc).label("avg_soc"),
            func.avg(Telemetry.soh).label("avg_soh"),
            func.avg(Telemetry.battery_temp).label("avg_temp"),
            func.avg(Telemetry.uptime_percentage).label("avg_uptime"),
        ).where(Telemetry.device_id == device_id, Telemetry.timestamp >= cutoff)
    )
    row = result.one()
    return {
        "device_id": device_id,
        "hours": hours,
        "samples": row.samples or 0,
        "avg_signal": round(row.avg_signal, 2) if row.avg_signal is not None else None,
        "min_signal": row.min_signal,
        "max_signal": row.max_signal,
        "avg_soc": round(row.avg_soc, 2) if row.avg_soc is not None else None,
        "avg_soh": round(row.avg_soh, 2) if row.avg_soh is not None else None,
        "avg_temp": round(row.avg_temp, 2) if row.avg_temp is not None else None,
        "avg_uptime": round(row.avg_uptime, 2) if row.avg_uptime is not None else None,
    }
