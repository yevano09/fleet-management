"""
Fleet Commander — Device Shadow / Digital Twin API (Feature 7)

AWS-IoT-style device shadows with desired vs reported state.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DeviceShadow, Device
from app.schemas import ShadowUpdateRequest, DeviceShadowResponse
from app.utils import utcnow
from app.audit import log_action
from app.mqtt_client import mqtt_client
from app.metrics import shadow_updates_total
from app.deps import require_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shadow", tags=["shadow"])


@router.get("/{device_id}")
async def get_shadow(device_id: str, principal: dict = Depends(require_user()), db: AsyncSession = Depends(get_db)):
    """Get the latest desired and reported shadow states for a device."""
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    if not dev_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Device not found")

    desired_result = await db.execute(
        select(DeviceShadow)
        .where(DeviceShadow.device_id == device_id, DeviceShadow.state == "desired")
        .order_by(DeviceShadow.version.desc()).limit(1)
    )
    reported_result = await db.execute(
        select(DeviceShadow)
        .where(DeviceShadow.device_id == device_id, DeviceShadow.state == "reported")
        .order_by(DeviceShadow.version.desc()).limit(1)
    )
    desired = desired_result.scalar_one_or_none()
    reported = reported_result.scalar_one_or_none()

    return {
        "device_id": device_id,
        "desired": _shadow_to_dict(desired) if desired else None,
        "reported": _shadow_to_dict(reported) if reported else None,
        "in_sync": _is_in_sync(desired, reported),
    }


@router.put("/{device_id}", response_model=DeviceShadowResponse)
async def update_shadow(
    device_id: str,
    req: ShadowUpdateRequest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Update the desired or reported shadow state for a device.

    MVP TWIN-01 optimistic concurrency: pass base_version with the latest
    version you read. A stale base is rejected with 409 (current version
    included) instead of silently overwriting another writer's state.

    When updating 'desired', the new state is pushed to the device via MQTT
    with its version so edge/device ends can reject stale pushes.
    """
    if req.state not in ("desired", "reported"):
        raise HTTPException(status_code=422, detail="state must be desired or reported")
    if (req.source or "cloud") not in ("cloud", "edge", "device"):
        raise HTTPException(status_code=422, detail="source must be cloud, edge or device")
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Latest version for this (device, state) chain.
    latest_result = await db.execute(
        select(DeviceShadow)
        .where(DeviceShadow.device_id == device_id, DeviceShadow.state == req.state)
        .order_by(DeviceShadow.version.desc()).limit(1)
    )
    latest = latest_result.scalar_one_or_none()
    latest_version = latest.version if latest else 0

    if req.base_version is not None and req.base_version != latest_version:
        raise HTTPException(status_code=409, detail={
            "message": "stale base_version: another writer updated this shadow",
            "attempted_base_version": req.base_version,
            "current": _shadow_to_dict(latest) if latest else None,
        })

    version = latest_version + 1
    expires_at = None
    if req.ttl_seconds:
        from datetime import timedelta
        expires_at = utcnow() + timedelta(seconds=req.ttl_seconds)

    shadow = DeviceShadow(
        device_id=device_id,
        state=req.state,
        payload=json.dumps(req.payload),
        version=version,
        supersedes_version=latest_version or None,
        source=req.source or "cloud",
        expires_at=expires_at,
        metadata_json=json.dumps({"updated_by": principal.get("email", "dashboard")}),
        timestamp=utcnow(),
    )
    db.add(shadow)
    await db.commit()
    await db.refresh(shadow)
    shadow_updates_total.labels(state=req.state).inc()
    await log_action(db, principal["email"], f"shadow.{req.state}_update", "device", device_id, {"version": version})

    # Push desired state to device via MQTT (carries versions for stale rejection)
    if req.state == "desired" and mqtt_client.is_connected:
        mqtt_client.publish_shadow_desired(device_id, req.payload, version=version, base_version=latest_version or None)

    return DeviceShadowResponse.model_validate(shadow)


@router.get("/{device_id}/history", response_model=list[DeviceShadowResponse])
async def get_shadow_history(
    device_id: str,
    state: Optional[str] = None,
    limit: int = 20,
    since_version: Optional[int] = None,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(DeviceShadow).where(DeviceShadow.device_id == device_id)
    if state:
        query = query.where(DeviceShadow.state == state)
    if since_version is not None:
        query = query.where(DeviceShadow.version > since_version)
    query = query.order_by(DeviceShadow.version.desc()).limit(limit)
    result = await db.execute(query)
    shadows = result.scalars().all()
    return [DeviceShadowResponse.model_validate(s) for s in shadows]


def _shadow_to_dict(shadow: DeviceShadow) -> dict:
    return {
        "state": shadow.state,
        "payload": json.loads(shadow.payload),
        "version": shadow.version,
        "supersedes_version": shadow.supersedes_version,
        "source": shadow.source,
        "timestamp": shadow.timestamp.isoformat() if shadow.timestamp else None,
    }


def _is_in_sync(desired: Optional[DeviceShadow], reported: Optional[DeviceShadow]) -> bool:
    if not desired:
        return True
    if not reported:
        return False
    return desired.payload == reported.payload
