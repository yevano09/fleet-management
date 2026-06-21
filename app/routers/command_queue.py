"""
Fleet Commander — Offline Command Queue API (Feature 5)

Queue commands for offline devices; delivered automatically on reconnect.
"""

from __future__ import annotations

import logging
import json
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CommandQueue, CommandStatus, Device
from app.schemas import CommandQueueRequest, CommandQueueResponse, CommandQueueListResponse
from app.utils import utcnow
from app.config import settings
from app.audit import log_action
from app.metrics import command_queue_delivered_total

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/commands", tags=["command-queue"])


@router.post("/queue", response_model=CommandQueueResponse, status_code=201)
async def queue_command(
    req: CommandQueueRequest,
    db: AsyncSession = Depends(get_db),
):
    """Queue a command for delivery to a device (immediately if online, on reconnect if offline)."""
    dev_result = await db.execute(select(Device).where(Device.id == req.device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    cmd = CommandQueue(
        device_id=req.device_id,
        command_type=req.command_type,
        payload=json.dumps(req.payload),
        status=CommandStatus.queued,
        expires_at=utcnow() + timedelta(seconds=req.ttl_seconds or settings.command_queue_ttl_seconds),
        max_retries=3,
    )
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    await log_action(db, "dashboard", "command.queue", "command", cmd.id, {
        "device_id": req.device_id, "command_type": req.command_type,
    })

    # If device is online, attempt immediate delivery via MQTT
    if device.status.value == "online" and device.last_seen and (utcnow() - device.last_seen).total_seconds() < 60:
        from app.mqtt_client import mqtt_client
        topic = f"iot/fleet/{req.device_id}/command/{req.command_type}"
        if mqtt_client.publish_raw(topic, json.dumps(req.payload)):
            cmd.status = CommandStatus.delivered
            cmd.delivered_at = utcnow()
            await db.commit()
            await db.refresh(cmd)
            command_queue_delivered_total.labels(command_type=req.command_type).inc()

    return CommandQueueResponse.model_validate(cmd)


@router.get("", response_model=CommandQueueListResponse)
async def list_commands(
    device_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(CommandQueue)
    count_query = select(func.count()).select_from(CommandQueue)
    if device_id:
        query = query.where(CommandQueue.device_id == device_id)
        count_query = count_query.where(CommandQueue.device_id == device_id)
    if status:
        query = query.where(CommandQueue.status == CommandStatus(status))
        count_query = count_query.where(CommandQueue.status == CommandStatus(status))
    query = query.order_by(CommandQueue.created_at.desc())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    commands = result.scalars().all()
    return CommandQueueListResponse(
        commands=[CommandQueueResponse.model_validate(c) for c in commands],
        total=total,
    )


@router.get("/{command_id}", response_model=CommandQueueResponse)
async def get_command(command_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CommandQueue).where(CommandQueue.id == command_id))
    cmd = result.scalar_one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
    return CommandQueueResponse.model_validate(cmd)


@router.post("/{command_id}/retry")
async def retry_command(command_id: str, db: AsyncSession = Depends(get_db)):
    """Manually re-attempt delivery of a queued command."""
    result = await db.execute(select(CommandQueue).where(CommandQueue.id == command_id))
    cmd = result.scalar_one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
    from app.mqtt_client import mqtt_client
    topic = f"iot/fleet/{cmd.device_id}/command/{cmd.command_type}"
    payload = json.loads(cmd.payload)
    if mqtt_client.publish_raw(topic, json.dumps(payload)):
        cmd.status = CommandStatus.delivered
        cmd.delivered_at = utcnow()
        command_queue_delivered_total.labels(command_type=cmd.command_type).inc()
        await db.commit()
        return {"message": "Command delivered", "command_id": command_id}
    else:
        cmd.retry_count += 1
        await db.commit()
        return {"message": "Delivery failed, will retry on reconnect", "command_id": command_id}


@router.delete("/{command_id}")
async def cancel_command(command_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CommandQueue).where(CommandQueue.id == command_id))
    cmd = result.scalar_one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
    await db.delete(cmd)
    await db.commit()
    return {"message": "Command cancelled", "command_id": command_id}


@router.get("/pending/{device_id}", response_model=CommandQueueListResponse)
async def get_pending_commands(device_id: str, db: AsyncSession = Depends(get_db)):
    """List all queued (undelivered) commands for a device."""
    result = await db.execute(
        select(CommandQueue)
        .where(CommandQueue.device_id == device_id, CommandQueue.status == CommandStatus.queued)
        .order_by(CommandQueue.created_at.asc())
    )
    commands = result.scalars().all()
    return CommandQueueListResponse(
        commands=[CommandQueueResponse.model_validate(c) for c in commands],
        total=len(commands),
    )
