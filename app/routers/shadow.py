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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shadow", tags=["shadow"])


@router.get("/{device_id}")
async def get_shadow(device_id: str, db: AsyncSession = Depends(get_db)):
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
    db: AsyncSession = Depends(get_db),
):
    """Update the desired or reported shadow state for a device.

    When updating 'desired', the new state is pushed to the device via MQTT.
    """
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get current version
    count_result = await db.execute(
        select(func.count()).select_from(DeviceShadow)
        .where(DeviceShadow.device_id == device_id, DeviceShadow.state == req.state)
    )
    version = (count_result.scalar() or 0) + 1

    shadow = DeviceShadow(
        device_id=device_id,
        state=req.state,
        payload=json.dumps(req.payload),
        version=version,
        metadata_json=json.dumps({"updated_by": "dashboard"}),
        timestamp=utcnow(),
    )
    db.add(shadow)
    await db.commit()
    await db.refresh(shadow)
    shadow_updates_total.labels(state=req.state).inc()
    await log_action(db, "dashboard", f"shadow.{req.state}_update", "device", device_id, {"version": version})

    # Push desired state to device via MQTT
    if req.state == "desired" and mqtt_client.is_connected:
        mqtt_client.publish_shadow_desired(device_id, req.payload)

    return DeviceShadowResponse.model_validate(shadow)


@router.get("/{device_id}/history", response_model=list[DeviceShadowResponse])
async def get_shadow_history(
    device_id: str,
    state: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    query = select(DeviceShadow).where(DeviceShadow.device_id == device_id)
    if state:
        query = query.where(DeviceShadow.state == state)
    query = query.order_by(DeviceShadow.version.desc()).limit(limit)
    result = await db.execute(query)
    shadows = result.scalars().all()
    return [DeviceShadowResponse.model_validate(s) for s in shadows]


def _shadow_to_dict(shadow: DeviceShadow) -> dict:
    return {
        "state": shadow.state,
        "payload": json.loads(shadow.payload),
        "version": shadow.version,
        "timestamp": shadow.timestamp.isoformat() if shadow.timestamp else None,
    }


def _is_in_sync(desired: Optional[DeviceShadow], reported: Optional[DeviceShadow]) -> bool:
    if not desired:
        return True
    if not reported:
        return False
    return desired.payload == reported.payload
