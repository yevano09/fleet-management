"""
Fleet Commander — Device Lifecycle API (Feature 9)

Device decommissioning, maintenance mode, and QR-claim provisioning flows.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_user, require_role
from app.models import Device, DeviceLifecycle, DeviceStatus
from app.schemas import DecommissionRequest, ClaimDeviceRequest, DeviceResponse
from app.utils import utcnow
from app.audit import log_action
from app.mqtt_client import mqtt_client
from app.metrics import device_lifecycle_transitions
from app.event_emitter import emit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


@router.post("/{device_id}/decommission")
async def decommission_device(
    device_id: str,
    req: DecommissionRequest,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    """Decommission a device — mark as retired, optionally factory reset."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.lifecycle_status == DeviceLifecycle.decommissioned:
        raise HTTPException(status_code=409, detail="Device already decommissioned")

    actor = req.actor if req.actor != "system" else principal["email"]
    old_status = device.lifecycle_status.value if device.lifecycle_status else "active"
    device.lifecycle_status = DeviceLifecycle.decommissioned
    device.decommissioned_at = utcnow()
    device.decommissioned_by = actor
    device.decommissioned_reason = req.reason
    device.status = DeviceStatus.offline
    await db.commit()

    device_lifecycle_transitions.labels(from_status=old_status, to_status="decommissioned").inc()
    await log_action(db, actor, "device.decommission", "device", device_id, {"reason": req.reason, "factory_reset": req.factory_reset})
    await emit_event(db, "device.decommissioned", {"device_id": device_id, "reason": req.reason, "actor": actor})

    if req.factory_reset and mqtt_client.is_connected:
        mqtt_client.publish_maintenance_command(device_id, enter=True, reason="decommission_factory_reset")

    return {"message": f"Device '{device.name}' decommissioned", "device_id": device_id, "reason": req.reason}


@router.post("/{device_id}/maintenance")
async def enter_maintenance(
    device_id: str,
    reason: str = "scheduled_maintenance",
    actor: str = "system",
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Put a device into maintenance mode."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    actor = actor if actor != "system" else principal["email"]
    old_status = device.lifecycle_status.value if device.lifecycle_status else "active"
    device.lifecycle_status = DeviceLifecycle.maintenance
    await db.commit()

    device_lifecycle_transitions.labels(from_status=old_status, to_status="maintenance").inc()
    if mqtt_client.is_connected:
        mqtt_client.publish_maintenance_command(device_id, enter=True, reason=reason)
    await log_action(db, actor, "device.maintenance_enter", "device", device_id, {"reason": reason})
    await emit_event(db, "device.maintenance", {"device_id": device_id, "reason": reason, "action": "enter"})
    return {"message": f"Device '{device.name}' in maintenance mode", "device_id": device_id}


@router.post("/{device_id}/activate")
async def activate_device(
    device_id: str,
    actor: str = "system",
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Return a device from maintenance/decommissioned to active."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    actor = actor if actor != "system" else principal["email"]
    old_status = device.lifecycle_status.value if device.lifecycle_status else "active"
    device.lifecycle_status = DeviceLifecycle.active
    device.decommissioned_at = None
    await db.commit()

    device_lifecycle_transitions.labels(from_status=old_status, to_status="active").inc()
    if mqtt_client.is_connected:
        mqtt_client.publish_maintenance_command(device_id, enter=False, reason="activated")
    await log_action(db, actor, "device.activate", "device", device_id)
    await emit_event(db, "device.activated", {"device_id": device_id})
    return {"message": f"Device '{device.name}' activated", "device_id": device_id}


@router.post("/{device_id}/claim-token")
async def generate_claim_token(device_id: str, principal: dict = Depends(require_role("fleet_manager")), db: AsyncSession = Depends(get_db)):
    """Generate a QR-claim provisioning token for a pre-registered device."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    token = secrets.token_urlsafe(16)
    device.claim_token = token
    await db.commit()
    await log_action(db, "system", "device.claim_token_generated", "device", device_id)
    return {"device_id": device_id, "claim_token": token}


@router.post("/claim", response_model=DeviceResponse, status_code=201)
async def claim_device(req: ClaimDeviceRequest, db: AsyncSession = Depends(get_db)):
    """Claim a pre-registered device using its claim token (QR provisioning)."""
    result = await db.execute(select(Device).where(Device.claim_token == req.claim_token))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Invalid or expired claim token")

    device.name = req.name
    device.firmware_version = req.firmware_version
    device.ip_address = req.ip_address
    if req.mqtt_client_id:
        device.mqtt_client_id = req.mqtt_client_id
    device.status = DeviceStatus.online
    device.last_seen = utcnow()
    device.claim_token = None  # token consumed
    device.lifecycle_status = DeviceLifecycle.active
    await db.commit()
    await db.refresh(device)
    await log_action(db, "system", "device.claimed", "device", device.id, {"name": req.name})
    await emit_event(db, "device.claimed", {"device_id": device.id, "name": req.name})
    return DeviceResponse.model_validate(device)
