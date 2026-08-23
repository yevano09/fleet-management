"""
Fleet Commander — Provisioning API (Feature 13)

Bulk CSV device import and QR-claim provisioning helpers.
"""

from __future__ import annotations

import csv
import io
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Device, DeviceStatus, DeviceLifecycle
from app.schemas import BulkImportResponse, DeviceResponse
from app.utils import utcnow
from app.audit import log_action
from app.metrics import active_devices, total_devices
from app.event_emitter import emit_event
from app.deps import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


@router.post("/bulk-import", response_model=BulkImportResponse)
async def bulk_import_devices(
    file: UploadFile = File(...),
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk import devices from a CSV file.

    Expected columns: name, firmware_version, ip_address, mqtt_client_id, city
    The header row is required. Extra columns are ignored.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    content = await file.read()
    text = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    device_ids = []

    for row_num, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {row_num}: missing 'name' field")
            skipped += 1
            continue

        # Check for duplicate name
        dup_result = await db.execute(select(Device).where(Device.name == name))
        if dup_result.scalar_one_or_none():
            errors.append(f"Row {row_num}: device '{name}' already exists")
            skipped += 1
            continue

        device = Device(
            name=name,
            firmware_version=(row.get("firmware_version") or "1.0.0").strip(),
            ip_address=(row.get("ip_address") or "").strip(),
            mqtt_client_id=(row.get("mqtt_client_id") or "").strip() or None,
            city=(row.get("city") or "").strip() or None,
            status=DeviceStatus.offline,
            lifecycle_status=DeviceLifecycle.active,
            last_seen=utcnow(),
            claim_token=secrets.token_urlsafe(16),
        )
        db.add(device)
        await db.flush()
        await db.refresh(device)
        device_ids.append(device.id)
        total_devices.inc()
        imported += 1

    await db.commit()
    if imported:
        await log_action(db, principal["email"], "device.bulk_import", "device", None, {"imported": imported, "skipped": skipped})
        await emit_event(db, "device.bulk_imported", {"imported": imported, "skipped": skipped})

    return BulkImportResponse(
        imported=imported, skipped=skipped, errors=errors, device_ids=device_ids,
    )


@router.post("/pre-register", response_model=DeviceResponse, status_code=201)
async def pre_register_device(
    name: str,
    firmware_version: str = "1.0.0",
    city: str = "",
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    """Pre-register a device and generate a QR-claim token.

    The device is created in 'offline' state with a claim token. A physical
    device can then claim itself using POST /lifecycle/claim with the token.
    """
    dup_result = await db.execute(select(Device).where(Device.name == name))
    if dup_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Device '{name}' already exists")

    token = secrets.token_urlsafe(16)
    device = Device(
        name=name,
        firmware_version=firmware_version,
        city=city or None,
        status=DeviceStatus.offline,
        lifecycle_status=DeviceLifecycle.active,
        last_seen=utcnow(),
        claim_token=token,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    total_devices.inc()
    await log_action(db, principal["email"], "device.pre_register", "device", device.id, {"name": name})
    return DeviceResponse.model_validate(device)
