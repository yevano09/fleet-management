"""
Fleet Commander — Geofencing API (Feature 2)

CRUD for geofences and geofence event history.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Geofence, GeofenceEvent, GeofenceShape
from app.schemas import (
    GeofenceCreateRequest, GeofenceResponse, GeofenceListResponse,
    GeofenceEventResponse,
)
from app.utils import utcnow
from app.deps import require_user, require_role
from app.deps import require_user, require_role
from app.audit import log_action
from app.metrics import geofence_active

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geofences", tags=["geofences"])


@router.get("", response_model=GeofenceListResponse)
async def list_geofences(
    enabled: Optional[bool] = Query(None),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(Geofence)
    if enabled is not None:
        query = query.where(Geofence.enabled == enabled)
    result = await db.execute(query.order_by(Geofence.created_at.desc()))
    geofences = result.scalars().all()
    geofence_active.set(sum(1 for g in geofences if g.enabled))
    return GeofenceListResponse(
        geofences=[GeofenceResponse.model_validate(g) for g in geofences],
        total=len(geofences),
    )


@router.post("", response_model=GeofenceResponse, status_code=201)
async def create_geofence(
    req: GeofenceCreateRequest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    if req.shape == "circle" and (req.center_lat is None or req.center_lng is None or req.radius_meters is None):
        raise HTTPException(status_code=400, detail="Circle geofence requires center_lat, center_lng, radius_meters")
    if req.shape == "polygon" and not req.polygon_coords:
        raise HTTPException(status_code=400, detail="Polygon geofence requires polygon_coords")

    gf = Geofence(
        name=req.name,
        shape=GeofenceShape(req.shape),
        center_lat=req.center_lat,
        center_lng=req.center_lng,
        radius_meters=req.radius_meters,
        polygon_coords=req.polygon_coords,
        device_ids=req.device_ids,
        alert_on_enter=req.alert_on_enter,
        alert_on_exit=req.alert_on_exit,
        color=req.color,
        enabled=req.enabled,
    )
    db.add(gf)
    await db.commit()
    await db.refresh(gf)
    await log_action(db, principal["email"], "geofence.create", "geofence", gf.id, {"name": req.name})
    return GeofenceResponse.model_validate(gf)


@router.get("/{geofence_id}", response_model=GeofenceResponse)
async def get_geofence(geofence_id: str, principal: dict = Depends(require_user()), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Geofence).where(Geofence.id == geofence_id))
    gf = result.scalar_one_or_none()
    if not gf:
        raise HTTPException(status_code=404, detail="Geofence not found")
    return GeofenceResponse.model_validate(gf)


@router.delete("/{geofence_id}")
async def delete_geofence(geofence_id: str, principal: dict = Depends(require_role("operator")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Geofence).where(Geofence.id == geofence_id))
    gf = result.scalar_one_or_none()
    if not gf:
        raise HTTPException(status_code=404, detail="Geofence not found")
    await db.delete(gf)
    await db.commit()
    await log_action(db, principal["email"], "geofence.delete", "geofence", geofence_id)
    return {"message": f"Geofence '{gf.name}' deleted"}


@router.patch("/{geofence_id}/toggle")
async def toggle_geofence(geofence_id: str, enabled: bool = True, principal: dict = Depends(require_role("operator")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Geofence).where(Geofence.id == geofence_id))
    gf = result.scalar_one_or_none()
    if not gf:
        raise HTTPException(status_code=404, detail="Geofence not found")
    gf.enabled = enabled
    await db.commit()
    return {"message": f"Geofence '{gf.name}' {'enabled' if enabled else 'disabled'}"}


@router.get("/{geofence_id}/events", response_model=list[GeofenceEventResponse])
async def get_geofence_events(
    geofence_id: str,
    limit: int = Query(50, ge=1, le=500),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GeofenceEvent)
        .where(GeofenceEvent.geofence_id == geofence_id)
        .order_by(GeofenceEvent.timestamp.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [GeofenceEventResponse.model_validate(e) for e in events]


@router.get("/events/all", response_model=list[GeofenceEventResponse])
async def get_all_events(
    device_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(GeofenceEvent)
    if device_id:
        query = query.where(GeofenceEvent.device_id == device_id)
    if event_type:
        query = query.where(GeofenceEvent.event_type == event_type)
    query = query.order_by(GeofenceEvent.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()
    return [GeofenceEventResponse.model_validate(e) for e in events]
