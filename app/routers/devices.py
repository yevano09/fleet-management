import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role, require_user, allowed_orgs, scope_devices
from app.models import Device, DeviceStatus
from app.schemas import (
    DeviceRegisterRequest, DeviceRegisterResponse,
    HeartbeatRequest, DeviceResponse, DeviceListResponse,
)
from pydantic import BaseModel
from app.metrics import active_devices, total_devices
from app.mqtt_client import mqtt_client
from app.utils import utcnow
from app.config import DEFAULT_ORG_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DeviceRegisterResponse, status_code=201)
async def register_device(
    req: DeviceRegisterRequest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    # P0 UC-26: REST-registered devices land in the caller's organization.
    scope = allowed_orgs(principal)
    org_id = scope[0] if scope else DEFAULT_ORG_ID

    result = await db.execute(select(Device).where(Device.name == req.name))
    existing = result.scalar_one_or_none()

    if existing:
        was_offline = existing.status == DeviceStatus.offline
        existing.status = DeviceStatus.online
        existing.last_seen = utcnow()
        existing.ip_address = req.ip_address or existing.ip_address
        if req.mqtt_client_id:
            existing.mqtt_client_id = req.mqtt_client_id
        if req.city:
            existing.city = req.city
        if was_offline:
            active_devices.inc()
        await db.commit()
        await db.refresh(existing)
        logger.info("Device re-registered: %s (%s)", existing.id, existing.name)
        return DeviceRegisterResponse(
            device_id=existing.id,
            name=existing.name,
            firmware_version=existing.firmware_version,
            status=existing.status.value,
            mqtt_client_id=existing.mqtt_client_id,
            city=existing.city,
        )

    device = Device(
        id=req.device_id or None,  # P0 UC-25: allow callers to pin id == cert CN
        name=req.name,
        firmware_version=req.firmware_version,
        status=DeviceStatus.online,
        last_seen=utcnow(),
        ip_address=req.ip_address,
        mqtt_client_id=req.mqtt_client_id,
        city=req.city,
        org_id=org_id,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)

    total_devices.inc()
    active_devices.inc()

    logger.info("Device registered: %s (%s) org=%s", device.id, device.name, org_id)
    return DeviceRegisterResponse(
        device_id=device.id,
        name=device.name,
        firmware_version=device.firmware_version,
        status=device.status.value,
        mqtt_client_id=device.mqtt_client_id,
        city=device.city,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: str,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one device. Cross-tenant access returns 404 (no existence leak)."""
    query = select(Device).where(Device.id == device_id)
    orgs = allowed_orgs(principal)
    if orgs is not None:
        query = query.where(Device.org_id.in_(orgs))
    result = await db.execute(query)
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceResponse.model_validate(device)


@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str,
    req: HeartbeatRequest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.last_seen = utcnow()
    device.uptime_percentage = req.uptime_percentage
    device.signal_strength = req.signal_strength
    device.status = DeviceStatus.online
    if req.city:
        device.city = req.city
    if req.soc is not None:
        device.soc = req.soc
    if req.soh is not None:
        device.soh = req.soh
    if req.battery_temp is not None:
        device.battery_temp = req.battery_temp
    if req.plug_status:
        device.plug_status = req.plug_status
    if req.latitude is not None and req.longitude is not None:
        device.latitude = req.latitude
        device.longitude = req.longitude
    await db.commit()

    return {"status": "ok", "last_seen": device.last_seen.isoformat()}


class RemoteConfigRequest(BaseModel):
    config: dict


@router.post("/{device_id}/config")
async def push_remote_config(
    device_id: str,
    req: RemoteConfigRequest,
    principal: dict = Depends(require_role("operator")),
):
    """Push a remote configuration to a device via MQTT."""
    mqtt_topic_id = device_id
    success = mqtt_client.publish_remote_config(mqtt_topic_id, req.config)
    if not success:
        raise HTTPException(status_code=503, detail="MQTT broker not connected")
    logger.info("Remote config pushed to device %s: %s", device_id, req.config)
    return {"status": "config_published", "device_id": device_id}


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    status: str = Query(None),
    lifecycle: str = Query(None, description="Filter by lifecycle: active, maintenance, decommissioned"),
    include_decommissioned: bool = Query(False, description="Include decommissioned devices"),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    # P0 UC-26: tenant scoping — callers only ever see their own org's devices.
    query = scope_devices(select(Device), principal)
    if status:
        query = query.where(Device.status == DeviceStatus(status))
    if lifecycle:
        from app.models import DeviceLifecycle
        query = query.where(Device.lifecycle_status == DeviceLifecycle(lifecycle))
    elif not include_decommissioned:
        from app.models import DeviceLifecycle
        query = query.where(Device.lifecycle_status != DeviceLifecycle.decommissioned)

    result = await db.execute(query.order_by(Device.last_seen.desc()))
    devices = result.scalars().all()

    now = utcnow()
    for device in devices:
        if device.status == DeviceStatus.online:
            elapsed = (now - device.last_seen).total_seconds()
            if elapsed > 60:
                device.status = DeviceStatus.offline
    # No DB commit — offline is a transient display signal, not a persisted state.
    # The heartbeat handler keeps the device online while it's active.

    return DeviceListResponse(
        devices=[DeviceResponse.model_validate(d) for d in devices],
        total=len(devices),
    )
