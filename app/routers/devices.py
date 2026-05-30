import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Device, DeviceStatus
from app.schemas import (
    DeviceRegisterRequest, DeviceRegisterResponse,
    HeartbeatRequest, DeviceResponse, DeviceListResponse,
)
from app.metrics import active_devices, total_devices
from app.utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DeviceRegisterResponse, status_code=201)
async def register_device(req: DeviceRegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == req.name))
    existing = result.scalar_one_or_none()

    if existing:
        was_offline = existing.status == DeviceStatus.offline
        existing.status = DeviceStatus.online
        existing.last_seen = utcnow()
        existing.ip_address = req.ip_address or existing.ip_address
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
        )

    device = Device(
        name=req.name,
        firmware_version=req.firmware_version,
        status=DeviceStatus.online,
        last_seen=utcnow(),
        ip_address=req.ip_address,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)

    total_devices.inc()
    active_devices.inc()

    logger.info("Device registered: %s (%s)", device.id, device.name)
    return DeviceRegisterResponse(
        device_id=device.id,
        name=device.name,
        firmware_version=device.firmware_version,
        status=device.status.value,
    )


@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str, req: HeartbeatRequest, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.last_seen = utcnow()
    device.uptime_percentage = req.uptime_percentage
    device.signal_strength = req.signal_strength
    device.status = DeviceStatus.online
    await db.commit()

    return {"status": "ok", "last_seen": device.last_seen.isoformat()}


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Device)
    if status:
        query = query.where(Device.status == DeviceStatus(status))

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
