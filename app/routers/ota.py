import os
import hashlib
import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Device, Firmware, OtaDeployment, OtaStatus, DeviceStatus
from app.schemas import (
    FirmwareUploadResponse, OtaTriggerRequest, OtaDeploymentResponse, OtaStatusResponse,
)
from app.mqtt_client import mqtt_client
from app.ota_manager import OtaStateMachine, ota_timeout_watcher
from app.metrics import ota_deployments_total, ota_deployments_in_progress
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ota", tags=["ota"])


@router.post("/upload", response_model=FirmwareUploadResponse)
async def upload_firmware(
    version: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    os.makedirs(settings.firmware_storage_path, exist_ok=True)

    existing = await db.execute(select(Firmware).where(Firmware.version == version))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Firmware version '{version}' already exists",
        )

    content = await file.read()
    sha256_hash = hashlib.sha256(content).hexdigest()
    file_path = os.path.join(settings.firmware_storage_path, file.filename)

    with open(file_path, "wb") as f:
        f.write(content)

    firmware = Firmware(
        version=version,
        filename=file.filename,
        sha256_hash=sha256_hash,
        binary_path=file_path,
        file_size=len(content),
    )
    db.add(firmware)
    await db.commit()
    await db.refresh(firmware)

    logger.info(f"Firmware uploaded: {firmware.version} ({firmware.sha256_hash[:16]}...)")
    return FirmwareUploadResponse(
        id=firmware.id,
        version=firmware.version,
        filename=firmware.filename,
        sha256_hash=firmware.sha256_hash,
        file_size=firmware.file_size,
        created_at=firmware.created_at,
    )


@router.post("/trigger")
async def trigger_ota(request: Request, req: OtaTriggerRequest, db: AsyncSession = Depends(get_db)):
    firmware_result = await db.execute(select(Firmware).where(Firmware.id == req.firmware_id))
    firmware = firmware_result.scalar_one_or_none()
    if not firmware:
        raise HTTPException(status_code=404, detail="Firmware not found")

    if not mqtt_client.is_connected:
        logger.error("MQTT not connected - cannot trigger OTA")
        raise HTTPException(status_code=503, detail="MQTT broker not connected")

    if req.all_devices:
        device_result = await db.execute(
            select(Device).where(Device.status == DeviceStatus.online)
        )
        devices = device_result.scalars().all()
    elif req.device_ids:
        device_result = await db.execute(
            select(Device).where(Device.id.in_(req.device_ids))
        )
        devices = device_result.scalars().all()
    else:
        raise HTTPException(status_code=400, detail="Specify device_ids or set all_devices=true")

    if not devices:
        raise HTTPException(status_code=404, detail="No devices found for OTA update")

    deployment_ids = []
    base_url = str(request.url).rstrip("/").rsplit("/ota", 1)[0]
    firmware_url = f"{base_url}/firmware/{firmware.filename}"
    mqtt_failures = []

    for device in devices:
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

        success = mqtt_client.publish_ota_command(
            mqtt_topic_id, firmware_url, firmware.sha256_hash, deployment.id
        )

        if success:
            deployment.status = OtaStatus.downloading
            ota_deployments_in_progress.inc()
            ota_timeout_watcher.start_watch(deployment.id, mqtt_topic_id)
            logger.info(f"OTA command published to device {device.id} ({device.name}) via MQTT id {mqtt_topic_id}, deployment {deployment.id}")
        else:
            mqtt_failures.append(device.id)
            logger.error(f"Failed to publish OTA command to device {device.id} ({device.name})")

        deployment_ids.append(deployment.id)

    await db.commit()

    if mqtt_failures:
        ota_deployments_total.labels(status="mqtt_failed").inc(len(mqtt_failures))
        logger.warning(f"OTA partially failed: {len(mqtt_failures)}/{len(devices)} devices got no MQTT message")

    ota_deployments_total.labels(status="triggered").inc(len(deployment_ids) - len(mqtt_failures))

    logger.info(f"OTA triggered for {len(devices)} devices with firmware {firmware.version}")
    return {
        "message": f"OTA update triggered for {len(devices)} devices",
        "deployment_ids": deployment_ids,
        "mqtt_failures": mqtt_failures,
        "firmware_version": firmware.version,
    }


@router.get("/status", response_model=OtaStatusResponse)
async def get_ota_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(OtaDeployment).order_by(OtaDeployment.created_at.desc())
    )
    deployments = result.scalars().all()

    success_count = sum(1 for d in deployments if d.status == OtaStatus.success)
    failed_count = sum(
        1 for d in deployments if d.status in (OtaStatus.failed, OtaStatus.hash_mismatch, OtaStatus.rolled_back)
    )
    in_progress_count = sum(
        1 for d in deployments if d.status in (
            OtaStatus.pending, OtaStatus.downloading, OtaStatus.applying, OtaStatus.verifying
        )
    )

    return OtaStatusResponse(
        deployments=[OtaDeploymentResponse.model_validate(d) for d in deployments],
        total=len(deployments),
        success_count=success_count,
        failed_count=failed_count,
        in_progress_count=in_progress_count,
    )


@router.get("/firmware", response_model=List[FirmwareUploadResponse])
async def list_firmware(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Firmware).order_by(Firmware.created_at.desc()))
    firmware_list = result.scalars().all()
    return [FirmwareUploadResponse.model_validate(f) for f in firmware_list]

<<<<<<< HEAD
=======

>>>>>>> e2550b3839872243ae9a3e7fd8232f28acab28f4
@router.delete("/firmware/{firmware_id}")
async def delete_firmware(firmware_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Firmware).where(Firmware.id == firmware_id))
    firmware = result.scalar_one_or_none()
    if not firmware:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Check if any deployment references this firmware
    dep_result = await db.execute(
        select(OtaDeployment).where(OtaDeployment.firmware_id == firmware_id).limit(1)
    )
    if dep_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Cannot delete firmware with existing deployments")

    # Remove the binary file
    if os.path.exists(firmware.binary_path):
        os.remove(firmware.binary_path)

    await db.delete(firmware)
    await db.commit()

    logger.info(f"Firmware deleted: {firmware.version} ({firmware.filename})")
    return {"message": f"Firmware {firmware.version} deleted"}
