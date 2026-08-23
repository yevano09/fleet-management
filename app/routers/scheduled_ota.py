"""
Fleet Commander — Scheduled OTA API (Feature 4)

Schedule OTA campaigns for maintenance windows with blackout hours,
canary percentages, and automatic execution when the scheduled time arrives.
"""

from __future__ import annotations

import logging
from typing import Optional
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.models import OtaSchedule, ScheduleStatus, Device, DeviceStatus, OtaDeployment, OtaStatus
from app.schemas import OtaScheduleCreateRequest, OtaScheduleResponse, OtaScheduleListResponse
from app.utils import utcnow
from app.audit import log_action
from app.mqtt_client import mqtt_client
from app.ota_manager import ota_timeout_watcher
from app.metrics import ota_scheduled_total, ota_deployments_total, ota_deployments_in_progress
from app.event_emitter import emit_event
from app.deps import require_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ota/schedules", tags=["scheduled-ota"])


@router.post("", response_model=OtaScheduleResponse, status_code=201)
async def create_schedule(
    req: OtaScheduleCreateRequest,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    """Create a scheduled OTA campaign."""
    schedule = OtaSchedule(
        name=req.name,
        firmware_id=req.firmware_id,
        device_ids=",".join(req.device_ids) if req.device_ids else "",
        all_devices=req.all_devices,
        scheduled_for=req.scheduled_for,
        blackout_start_hour=req.blackout_start_hour,
        blackout_end_hour=req.blackout_end_hour,
        canary_percent=req.canary_percent,
        status=ScheduleStatus.scheduled,
        created_by=principal["email"],
        org_id=principal["org_id"] if principal.get("org_id") not in (None, "*") else "org-default",
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    ota_scheduled_total.labels(status="scheduled").inc()
    await log_action(db, principal["email"], "ota.schedule_create", "schedule", schedule.id, {"name": req.name})
    return OtaScheduleResponse.model_validate(schedule)


@router.get("", response_model=OtaScheduleListResponse)
async def list_schedules(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(OtaSchedule)
    count_query = select(func.count()).select_from(OtaSchedule)
    if status:
        query = query.where(OtaSchedule.status == ScheduleStatus(status))
        count_query = count_query.where(OtaSchedule.status == ScheduleStatus(status))
    query = query.order_by(OtaSchedule.scheduled_for.desc())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    schedules = result.scalars().all()
    return OtaScheduleListResponse(
        schedules=[OtaScheduleResponse.model_validate(s) for s in schedules],
        total=total,
    )


@router.get("/{schedule_id}", response_model=OtaScheduleResponse)
async def get_schedule(schedule_id: str, principal: dict = Depends(require_user()), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OtaSchedule).where(OtaSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return OtaScheduleResponse.model_validate(schedule)


@router.post("/{schedule_id}/cancel")
async def cancel_schedule(schedule_id: str, principal: dict = Depends(require_role("fleet_manager")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OtaSchedule).where(OtaSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.status not in (ScheduleStatus.scheduled, ScheduleStatus.paused):
        raise HTTPException(status_code=409, detail=f"Cannot cancel schedule in '{schedule.status.value}' state")
    schedule.status = ScheduleStatus.cancelled
    await db.commit()
    ota_scheduled_total.labels(status="cancelled").inc()
    await log_action(db, principal["email"], "ota.schedule_cancel", "schedule", schedule_id)
    return {"message": "Schedule cancelled", "schedule_id": schedule_id}


@router.post("/{schedule_id}/pause")
async def pause_schedule(schedule_id: str, principal: dict = Depends(require_role("fleet_manager")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OtaSchedule).where(OtaSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.status != ScheduleStatus.scheduled:
        raise HTTPException(status_code=409, detail="Only scheduled campaigns can be paused")
    schedule.status = ScheduleStatus.paused
    await db.commit()
    return {"message": "Schedule paused", "schedule_id": schedule_id}


@router.post("/{schedule_id}/resume")
async def resume_schedule(schedule_id: str, principal: dict = Depends(require_role("fleet_manager")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OtaSchedule).where(OtaSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.status != ScheduleStatus.paused:
        raise HTTPException(status_code=409, detail="Only paused campaigns can be resumed")
    schedule.status = ScheduleStatus.scheduled
    await db.commit()
    return {"message": "Schedule resumed", "schedule_id": schedule_id}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, principal: dict = Depends(require_role("fleet_manager")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OtaSchedule).where(OtaSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await db.delete(schedule)
    await db.commit()
    return {"message": "Schedule deleted", "schedule_id": schedule_id}


async def run_due_schedules(db: AsyncSession):
    """Execute scheduled OTA campaigns that are due, respecting blackout windows.

    Called by the OTA scheduler background loop in main.py.
    """
    now = utcnow()
    result = await db.execute(
        select(OtaSchedule).where(
            OtaSchedule.status == ScheduleStatus.scheduled,
            OtaSchedule.scheduled_for <= now,
        )
    )
    due = result.scalars().all()

    for schedule in due:
        # Check blackout window
        if schedule.blackout_start_hour is not None and schedule.blackout_end_hour is not None:
            hour = now.hour
            if schedule.blackout_start_hour <= hour < schedule.blackout_end_hour:
                logger.info("Schedule %s in blackout window (hour=%d), deferring", schedule.id[:8], hour)
                continue

        try:
            schedule.status = ScheduleStatus.running
            schedule.started_at = now
            await db.commit()

            # Resolve firmware
            from app.models import Firmware
            fw_result = await db.execute(select(Firmware).where(Firmware.id == schedule.firmware_id))
            firmware = fw_result.scalar_one_or_none()
            if not firmware:
                schedule.status = ScheduleStatus.failed
                schedule.error_message = "Firmware not found"
                schedule.completed_at = utcnow()
                await db.commit()
                ota_scheduled_total.labels(status="failed").inc()
                continue

            # Resolve devices
            if schedule.all_devices:
                dev_result = await db.execute(select(Device).where(Device.status == DeviceStatus.online))
                devices = dev_result.scalars().all()
            else:
                ids = [d.strip() for d in schedule.device_ids.split(",") if d.strip()]
                if not ids:
                    schedule.status = ScheduleStatus.failed
                    schedule.error_message = "No devices specified"
                    schedule.completed_at = utcnow()
                    await db.commit()
                    ota_scheduled_total.labels(status="failed").inc()
                    continue
                dev_result = await db.execute(select(Device).where(Device.id.in_(ids)))
                devices = dev_result.scalars().all()

            if not devices:
                schedule.status = ScheduleStatus.failed
                schedule.error_message = "No online devices found"
                schedule.completed_at = utcnow()
                await db.commit()
                ota_scheduled_total.labels(status="failed").inc()
                continue

            base_url = settings_ota_base_url()
            firmware_url = f"{base_url}/firmware/{firmware.filename}"
            deployment_ids = []

            # Canary first
            canary_count = max(1, int(len(devices) * schedule.canary_percent / 100))
            for device in devices[:canary_count]:
                dep = await _create_deployment(db, device, firmware, firmware_url)
                if dep:
                    deployment_ids.append(dep.id)

            # Then the rest
            for device in devices[canary_count:]:
                dep = await _create_deployment(db, device, firmware, firmware_url)
                if dep:
                    deployment_ids.append(dep.id)

            schedule.status = ScheduleStatus.completed
            schedule.completed_at = utcnow()
            schedule.deployment_ids = ",".join(deployment_ids)
            await db.commit()
            ota_scheduled_total.labels(status="completed").inc()
            await emit_event(db, "ota.schedule_completed", {
                "schedule_id": schedule.id, "name": schedule.name,
                "deployments": len(deployment_ids),
            })
            logger.info("Schedule %s completed: %d deployments", schedule.id[:8], len(deployment_ids))
        except Exception as e:
            schedule.status = ScheduleStatus.failed
            schedule.error_message = str(e)
            schedule.completed_at = utcnow()
            await db.commit()
            ota_scheduled_total.labels(status="failed").inc()
            logger.exception("Schedule %s failed", schedule.id[:8])


async def _create_deployment(db, device, firmware, firmware_url):
    """Create an OTA deployment and publish the command."""
    deployment = OtaDeployment(
        firmware_id=firmware.id,
        device_id=device.id,
        firmware_url=firmware_url,
        status=OtaStatus.pending,
    )
    db.add(deployment)
    await db.flush()
    await db.refresh(deployment)

    mqtt_topic_id = device.mqtt_client_id or device.id
    device.current_ota_id = deployment.id
    device.previous_firmware_version = device.firmware_version

    dl_token, dl_exp = ("", 0)
    if settings.auth_mode == "strict":
        from app.main import issue_firmware_download_token
        dl_token, dl_exp = issue_firmware_download_token(device.id, firmware.sha256_hash)

    success = mqtt_client.publish_ota_command(
        mqtt_topic_id, firmware_url, firmware.sha256_hash, deployment.id,
        download_token=dl_token, token_exp=dl_exp,
    )
    if success:
        deployment.status = OtaStatus.downloading
        ota_deployments_in_progress.inc()
        ota_deployments_total.labels(status="triggered").inc()
        ota_timeout_watcher.start_watch(deployment.id, mqtt_topic_id)
    else:
        ota_deployments_total.labels(status="mqtt_failed").inc()
    return deployment


def settings_ota_base_url():
    from app.config import settings
    return settings.ota_firmware_base_url
