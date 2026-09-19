"""
Fleet Commander — Smart Cargo API (SRS Idea 4).

Cold-chain profiles, cargo telemetry reads, shock-event history.
Live ingest arrives over MQTT (+/cargo); POST exists for tooling/tests,
mirroring the REST heartbeat precedent.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role, require_user, allowed_orgs
from app.models import CargoProfile, CargoReading, Device, ShockEvent
from app.schemas import (
    CargoProfileResponse,
    CargoProfileUpdate,
    CargoReadingIngest,
    CargoReadingResponse,
    ShockEventResponse,
)
from app.utils import utcnow
from app.metrics import cargo_readings_total

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cargo", tags=["cargo"])


def _scope_profile(query, principal):
    orgs = allowed_orgs(principal)
    if orgs is not None:
        query = query.where(CargoProfile.org_id.in_(orgs))
    return query


@router.get("/{device_id}/profile", response_model=CargoProfileResponse)
async def get_profile(
    device_id: str,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        _scope_profile(select(CargoProfile).where(CargoProfile.device_id == device_id), principal)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        # Default contract — a device without a profile uses safe defaults.
        return CargoProfileResponse(device_id=device_id)
    return CargoProfileResponse.model_validate(profile)


@router.put("/{device_id}/profile", response_model=CargoProfileResponse)
async def upsert_profile(
    device_id: str,
    req: CargoProfileUpdate,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    dev = await db.execute(select(Device).where(Device.id == device_id))
    device = dev.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    result = await db.execute(select(CargoProfile).where(CargoProfile.device_id == device_id))
    profile = result.scalar_one_or_none()
    if not profile:
        orgs = allowed_orgs(principal)
        profile = CargoProfile(device_id=device_id, org_id=(orgs[0] if orgs else device.org_id))
        db.add(profile)
    for field in ("commodity", "temp_min_c", "temp_max_c", "thermal_mass", "door_alerts", "trip_eta_minutes"):
        value = getattr(req, field)
        if value is not None:
            setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return CargoProfileResponse.model_validate(profile)


@router.post("/{device_id}/readings", response_model=CargoReadingResponse, status_code=201)
async def ingest_reading(
    device_id: str,
    req: CargoReadingIngest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """REST ingest mirror of the MQTT +/cargo path (tooling/tests/offline replay)."""
    dev = await db.execute(select(Device).where(Device.id == device_id))
    device = dev.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    row = CargoReading(
        device_id=device.id,
        timestamp=utcnow(),
        bay_temp_c=req.bay_temp_c,
        humidity_pct=req.humidity_pct,
        door_open=req.door_open,
        shock_g=req.shock_g,
        source=req.source,
        ai_inference=json.dumps(req.ai_inference) if req.ai_inference is not None else None,
        tenant_id=device.org_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    cargo_readings_total.labels(source=row.source).inc()
    return CargoReadingResponse.model_validate(row)


@router.get("/{device_id}/readings")
async def list_readings(
    device_id: str,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(200, ge=1, le=2000),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta

    cutoff = utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(CargoReading)
        .where(CargoReading.device_id == device_id, CargoReading.timestamp >= cutoff)
        .order_by(CargoReading.timestamp.asc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return {
        "device_id": device_id,
        "points": [CargoReadingResponse.model_validate(r) for r in rows],
        "total": len(rows),
    }


@router.get("/{device_id}/shocks")
async def list_shocks(
    device_id: str,
    limit: int = Query(50, ge=1, le=500),
    event_class: Optional[str] = Query(None),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(ShockEvent).where(ShockEvent.device_id == device_id)
    if event_class:
        query = query.where(ShockEvent.event_class == event_class)
    result = await db.execute(query.order_by(ShockEvent.timestamp.desc()).limit(limit))
    rows = result.scalars().all()
    return {
        "device_id": device_id,
        "events": [ShockEventResponse.model_validate(r) for r in rows],
        "total": len(rows),
    }


@router.get("/overview")
async def cargo_overview(
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """Fleet cold-chain rollup for the dashboard cargo panel + copilot."""
    from app.cargo_thermal import estimate_tts

    result = await db.execute(_scope_profile(select(CargoProfile), principal))
    profs = result.scalars().all()
    loads = []
    for p in profs:
        rows = (await db.execute(
            select(CargoReading)
            .where(CargoReading.device_id == p.device_id)
            .order_by(CargoReading.timestamp.desc())
            .limit(12)
        )).scalars().all()
        if not rows:
            loads.append({"device_id": p.device_id, "commodity": p.commodity,
                          "bay_temp_c": None, "tts_minutes": None, "risk": "UNKNOWN",
                          "door_open": None})
            continue
        asc = [(r.timestamp, r.bay_temp_c) for r in reversed(rows)]
        est = estimate_tts(asc, p.temp_max_c, p.thermal_mass or 1.0)
        loads.append({"device_id": p.device_id, "commodity": p.commodity,
                      "bay_temp_c": rows[0].bay_temp_c,
                      "humidity_pct": rows[0].humidity_pct,
                      "door_open": rows[0].door_open,
                      "tts_minutes": est["tts_minutes"], "risk": est["risk_level"]})
    high = sum(1 for l in loads if l["risk"] == "HIGH")
    return {"loads": loads, "total": len(loads), "high_risk_count": high}


@router.post("/scan")
async def run_shock_scan(
    lookback_hours: int = Query(1, ge=1, le=24),
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Classify recent shock peaks into ShockEvent rows (M1.3 heuristic v1)."""
    from app.shock_classifier import scan_shocks

    events = await scan_shocks(db, lookback_hours=lookback_hours)
    return {
        "message": f"Shock scan completed. {len(events)} events classified.",
        "events_count": len(events),
        "events": [
            {"device_id": e.device_id, "event_class": e.event_class,
             "peak_g": e.peak_g, "model_version": e.model_version}
            for e in events
        ],
    }
