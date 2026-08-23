import os
import hashlib
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role, require_user, allowed_orgs, scope_devices
from app.models import Device, Firmware, OtaDeployment, OtaStatus, DeviceStatus
from app.schemas import (
    FirmwareUploadResponse, OtaTriggerRequest, OtaDeploymentResponse, OtaStatusResponse,
)
from app.mqtt_client import mqtt_client
from app.ota_manager import OtaStateMachine, ota_timeout_watcher
from app.metrics import ota_deployments_total, ota_deployments_in_progress, firmware_signed_total
from app.config import settings
from app.audit import log_action
from app.event_emitter import emit_event
from app.firmware_signing import sign_firmware

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ota", tags=["ota"])


@router.post("/upload", response_model=FirmwareUploadResponse)
async def upload_firmware(
    version: str = Form(...),
    file: UploadFile = File(...),
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    if file.size and file.size > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {settings.max_upload_size_mb}MB limit",
        )

    existing = await db.execute(select(Firmware).where(Firmware.version == version))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Firmware version '{version}' already exists",
        )

    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {settings.max_upload_size_mb}MB limit",
        )
    sha256_hash = hashlib.sha256(content).hexdigest()
    safe_filename = os.path.basename(file.filename or "firmware.bin")
    file_path = os.path.join(settings.firmware_storage_path, safe_filename)

    with open(file_path, "wb") as f:
        f.write(content)

    # Feature 8: Cryptographically sign the firmware (if signing key configured)
    signature_hex, key_id = sign_firmware(content)
    signed_by = "system" if signature_hex else None
    if signature_hex:
        firmware_signed_total.inc()

    firmware = Firmware(
        version=version,
        filename=safe_filename,
        sha256_hash=sha256_hash,
        binary_path=file_path,
        file_size=len(content),
        signature=signature_hex,
        signing_key_id=key_id,
        signed_by=signed_by,
        org_id=principal["org_id"] if principal.get("org_id") not in (None, "*") else "org-default",
    )
    db.add(firmware)
    await db.commit()
    await db.refresh(firmware)

    await log_action(db, principal["email"], "firmware.upload", "firmware", firmware.id, {
        "version": version, "signed": bool(signature_hex),
    })
    logger.info("Firmware uploaded: %s (%s...) signed=%s", firmware.version, firmware.sha256_hash[:16], bool(signature_hex))
    return FirmwareUploadResponse(
        id=firmware.id,
        version=firmware.version,
        filename=firmware.filename,
        sha256_hash=firmware.sha256_hash,
        file_size=firmware.file_size,
        created_at=firmware.created_at,
        signature=firmware.signature,
        signing_key_id=firmware.signing_key_id,
        signed_by=firmware.signed_by,
    )


@router.post("/trigger")
async def trigger_ota(
    request: Request,
    req: OtaTriggerRequest,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    fw_query = select(Firmware).where(Firmware.id == req.firmware_id)
    fw_scope = allowed_orgs(principal)
    if fw_scope is not None:
        fw_query = fw_query.where(Firmware.org_id.in_(fw_scope))
    firmware_result = await db.execute(fw_query)
    firmware = firmware_result.scalar_one_or_none()
    if not firmware:
        raise HTTPException(status_code=404, detail="Firmware not found")

    if not mqtt_client.is_connected:
        logger.error("MQTT not connected - cannot trigger OTA")
        raise HTTPException(status_code=503, detail="MQTT broker not connected")

    device_query = None
    if req.all_devices:
        device_query = select(Device).where(Device.status == DeviceStatus.online)
    elif req.device_ids:
        device_query = select(Device).where(Device.id.in_(req.device_ids))
    else:
        raise HTTPException(status_code=400, detail="Specify device_ids or set all_devices=true")

    # P0 UC-26: cross-tenant targets are invisible (404), never 403.
    device_query = scope_devices(device_query, principal)
    device_result = await db.execute(device_query)
    devices = device_result.scalars().all()

    if not devices:
        raise HTTPException(status_code=404, detail="No devices found for OTA update")

    deployment_ids = []
    base_url = str(request.url).rstrip("/").rsplit("/ota", 1)[0]
    firmware_url = f"{base_url}/firmware/{firmware.filename}"
    mqtt_failures = []

    strict = settings.auth_mode == "strict"
    from app.main import issue_firmware_download_token

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

        dl_token, dl_exp = ("", 0)
        if strict:
            dl_token, dl_exp = issue_firmware_download_token(device.id, firmware.sha256_hash)

        success = mqtt_client.publish_ota_command(
            mqtt_topic_id, firmware_url, firmware.sha256_hash, deployment.id,
            download_token=dl_token, token_exp=dl_exp,
        )

        if success:
            deployment.status = OtaStatus.downloading
            ota_deployments_in_progress.inc()
            ota_timeout_watcher.start_watch(deployment.id, mqtt_topic_id)
            logger.info("OTA command published to device %s (%s) via MQTT id %s, deployment %s", device.id, device.name, mqtt_topic_id, deployment.id)
        else:
            mqtt_failures.append(device.id)
            logger.error("Failed to publish OTA command to device %s (%s)", device.id, device.name)

        deployment_ids.append(deployment.id)

    await db.commit()

    if mqtt_failures:
        ota_deployments_total.labels(status="mqtt_failed").inc(len(mqtt_failures))
        logger.warning("OTA partially failed: %s/%s devices got no MQTT message", len(mqtt_failures), len(devices))

    ota_deployments_total.labels(status="triggered").inc(len(deployment_ids) - len(mqtt_failures))

    await log_action(db, principal["email"], "ota.trigger", "firmware", firmware.id, {
        "firmware_version": firmware.version, "device_count": len(devices),
        "deployment_ids": deployment_ids,
    })
    await emit_event(db, "ota.triggered", {
        "firmware_id": firmware.id, "firmware_version": firmware.version,
        "device_count": len(devices), "deployment_ids": deployment_ids,
    })

    logger.info("OTA triggered for %s devices with firmware %s", len(devices), firmware.version)
    return {
        "message": f"OTA update triggered for {len(devices)} devices",
        "deployment_ids": deployment_ids,
        "mqtt_failures": mqtt_failures,
        "firmware_version": firmware.version,
        "signature": firmware.signature,
    }


@router.get("/status", response_model=OtaStatusResponse)
async def get_ota_status(
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
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
async def list_firmware(
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(Firmware).order_by(Firmware.created_at.desc())
    fw_scope = allowed_orgs(principal)
    if fw_scope is not None:
        query = query.where(Firmware.org_id.in_(fw_scope))
    result = await db.execute(query)
    firmware_list = result.scalars().all()
    return [FirmwareUploadResponse.model_validate(f) for f in firmware_list]

@router.delete("/firmware/{firmware_id}")
async def delete_firmware(
    firmware_id: str,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
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

    await log_action(db, principal["email"], "firmware.delete", "firmware", firmware_id)
    logger.info("Firmware deleted: %s (%s)", firmware.version, firmware.filename)
    return {"message": f"Firmware {firmware.version} deleted"}
