"""
Fleet Commander — Geofence Checker (Feature 2)

Determines whether a device's GPS position is inside a geofence and
records enter/exit events, firing alerts via the alert engine.
"""

from __future__ import annotations

import json
import math
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Geofence, GeofenceEvent, GeofenceShape, Device
from app.utils import utcnow
from app.metrics import geofence_events_total

logger = logging.getLogger(__name__)


def _haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters between two lat/lng points."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _point_in_polygon(lat: float, lng: float, coords: list) -> bool:
    """Ray-casting point-in-polygon. coords = [[lat,lng], ...]."""
    n = len(coords)
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = coords[i][0], coords[i][1]
        yj, xj = coords[j][0], coords[j][1]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def is_inside(geofence: Geofence, lat: float, lng: float) -> bool:
    """Check if a point is inside the geofence."""
    if geofence.shape == GeofenceShape.circle:
        if geofence.center_lat is None or geofence.center_lng is None or geofence.radius_meters is None:
            return False
        dist = _haversine_meters(lat, lng, geofence.center_lat, geofence.center_lng)
        return dist <= geofence.radius_meters
    else:
        if not geofence.polygon_coords:
            return False
        try:
            coords = json.loads(geofence.polygon_coords)
            return _point_in_polygon(lat, lng, coords)
        except (json.JSONDecodeError, TypeError):
            return False


def _device_matches_geofence(geofence: Geofence, device_id: str) -> bool:
    """Check if this geofence applies to the given device."""
    if not geofence.device_ids:
        return True  # fleet-wide
    return device_id in geofence.device_ids.split(",")


async def check_device_position(
    db: AsyncSession,
    device: Device,
) -> list[GeofenceEvent]:
    """Check a device's current position against all enabled geofences.

    Returns a list of new geofence events (enter/exit) created.
    """
    if device.latitude is None or device.longitude is None:
        return []

    result = await db.execute(select(Geofence).where(Geofence.enabled == True))
    geofences = result.scalars().all()

    new_events = []
    now = utcnow()

    for gf in geofences:
        if not _device_matches_geofence(gf, device.id):
            continue
        inside = is_inside(gf, device.latitude, device.longitude)

        # Find the most recent event for this device+geofence
        evt_result = await db.execute(
            select(GeofenceEvent)
            .where(GeofenceEvent.geofence_id == gf.id, GeofenceEvent.device_id == device.id)
            .order_by(GeofenceEvent.timestamp.desc())
            .limit(1)
        )
        last_event = evt_result.scalar_one_or_none()

        was_inside = last_event is not None and last_event.event_type == "enter"

        if inside and not was_inside:
            # ENTER event
            evt = GeofenceEvent(
                geofence_id=gf.id,
                device_id=device.id,
                event_type="enter",
                latitude=device.latitude,
                longitude=device.longitude,
                timestamp=now,
                alerted=gf.alert_on_enter,
            )
            db.add(evt)
            new_events.append(evt)
            geofence_events_total.labels(event_type="enter").inc()
        elif not inside and was_inside:
            # EXIT event
            evt = GeofenceEvent(
                geofence_id=gf.id,
                device_id=device.id,
                event_type="exit",
                latitude=device.latitude,
                longitude=device.longitude,
                timestamp=now,
                alerted=gf.alert_on_exit,
            )
            db.add(evt)
            new_events.append(evt)
            geofence_events_total.labels(event_type="exit").inc()

    if new_events:
        await db.commit()

    return new_events


async def build_geofence_alerts(events: list[GeofenceEvent], db: AsyncSession) -> list[dict]:
    """Convert geofence events into anomaly dicts for the alert engine."""
    anomalies = []
    for evt in events:
        gf_result = await db.execute(select(Geofence).where(Geofence.id == evt.geofence_id))
        gf = gf_result.scalar_one_or_none()
        if not gf:
            continue
        device_result = await db.execute(select(Device).where(Device.id == evt.device_id))
        device = device_result.scalar_one_or_none()
        device_name = device.name if device else evt.device_id[:8]
        anomalies.append({
            "type": f"geofence_{evt.event_type}",
            "severity": "warning",
            "message": f"Device '{device_name}' {evt.event_type}ed geofence '{gf.name}'",
            "affected_device_ids": [evt.device_id],
            "timestamp": evt.timestamp.isoformat(),
        })
    return anomalies
