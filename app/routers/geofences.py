"""
Fleet Commander — Geofencing API (Feature 2)

CRUD for geofences and geofence event history.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Geofence, GeofenceEvent, GeofenceShape
from app.schemas import (
    GeofenceCreateRequest, GeofenceResponse, GeofenceListResponse,
    GeofenceEventResponse,
)
from app.utils import utcnow
from app.deps import require_user, require_role, allowed_orgs
from app.audit import log_action
from app.metrics import geofence_active

from app.config import DEFAULT_ORG_ID


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geofences", tags=["geofences"])


def _scope_geofences(query, principal: dict):
    """Tenant filter for Geofence queries (None scope = super-admin, unfiltered)."""
    orgs = allowed_orgs(principal)
    if orgs is not None:
        query = query.where(Geofence.org_id.in_(orgs))
    return query


async def _get_scoped_geofence(db, geofence_id: str, principal: dict):
    """Fetch a geofence only if the principal's org scope may touch it."""
    query = _scope_geofences(select(Geofence).where(Geofence.id == geofence_id), principal)
    result = await db.execute(query)
    return result.scalar_one_or_none()


@router.get("", response_model=GeofenceListResponse)
async def list_geofences(
    enabled: Optional[bool] = Query(None),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = _scope_geofences(select(Geofence), principal)
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

    orgs = allowed_orgs(principal)
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
        org_id=orgs[0] if orgs else DEFAULT_ORG_ID,
    )
    db.add(gf)
    await db.commit()
    await db.refresh(gf)
    await log_action(db, principal["email"], "geofence.create", "geofence", gf.id, {"name": req.name})
    return GeofenceResponse.model_validate(gf)


@router.get("/{geofence_id}", response_model=GeofenceResponse)
async def get_geofence(geofence_id: str, principal: dict = Depends(require_user()), db: AsyncSession = Depends(get_db)):
    gf = await _get_scoped_geofence(db, geofence_id, principal)
    if not gf:
        raise HTTPException(status_code=404, detail="Geofence not found")
    return GeofenceResponse.model_validate(gf)


@router.delete("/{geofence_id}")
async def delete_geofence(geofence_id: str, principal: dict = Depends(require_role("operator")), db: AsyncSession = Depends(get_db)):
    gf = await _get_scoped_geofence(db, geofence_id, principal)
    if not gf:
        raise HTTPException(status_code=404, detail="Geofence not found")
    await db.delete(gf)
    await db.commit()
    await log_action(db, principal["email"], "geofence.delete", "geofence", geofence_id)
    return {"message": f"Geofence '{gf.name}' deleted"}


@router.patch("/{geofence_id}/toggle")
async def toggle_geofence(geofence_id: str, enabled: bool = True, principal: dict = Depends(require_role("operator")), db: AsyncSession = Depends(get_db)):
    gf = await _get_scoped_geofence(db, geofence_id, principal)
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
    # Events inherit tenancy through their geofence — verify scope first so one
    # org cannot enumerate another org's geofence history by id.
    if not await _get_scoped_geofence(db, geofence_id, principal):
        raise HTTPException(status_code=404, detail="Geofence not found")
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
    # Fleet-wide event feed inherits tenancy via join — non-admin callers only
    # see events for geofences in their own org.
    query = select(GeofenceEvent).join(Geofence, GeofenceEvent.geofence_id == Geofence.id)
    orgs = allowed_orgs(principal)
    if orgs is not None:
        query = query.where(Geofence.org_id.in_(orgs))
    if device_id:
        query = query.where(GeofenceEvent.device_id == device_id)
    if event_type:
        query = query.where(GeofenceEvent.event_type == event_type)
    query = query.order_by(GeofenceEvent.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()
    return [GeofenceEventResponse.model_validate(e) for e in events]
