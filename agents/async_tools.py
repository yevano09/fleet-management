"""
Fleet Commander — Async Database Tools for In-Backend Agent Usage

These tools query the database directly via SQLAlchemy instead of
making HTTP calls to the backend API. This avoids the self-referencing
deadlock when agents run inside the backend container.

Usage:
  from agents.async_tools import async_list_devices, async_list_firmware, ...
  from app.database import get_db
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DeviceStatus, Firmware, OtaDeployment, OtaStatus

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def async_list_devices(db: AsyncSession, status: str | None = None) -> dict:
    """Fetch all devices from DB, optionally filtered by status.

    Returns: {devices: [...], total: N}  (same format as HTTP tools)
    """
    query = select(Device)
    if status:
        query = query.where(Device.status == DeviceStatus(status))
    result = await db.execute(query.order_by(Device.last_seen.desc()))
    devices = result.scalars().all()

    now = _utcnow()
    for device in devices:
        if device.status == DeviceStatus.online:
            elapsed = (now - device.last_seen).total_seconds()
            if elapsed > 60:
                device.status = DeviceStatus.offline
    await db.commit()

    return {
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "firmware_version": d.firmware_version,
                "status": d.status.value,
                "signal_strength": d.signal_strength or 0,
                "uptime_percentage": d.uptime_percentage or 0.0,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "ip_address": d.ip_address or "",
                "previous_firmware_version": d.previous_firmware_version or "",
            }
            for d in devices
        ],
        "total": len(devices),
    }


async def async_list_firmware(db: AsyncSession) -> list[dict]:
    """List all firmware versions from DB.

    Returns: [{id, version, filename, sha256_hash, file_size, created_at}, ...]
    """
    result = await db.execute(select(Firmware).order_by(Firmware.created_at.desc()))
    firmware_list = result.scalars().all()
    return [
        {
            "id": f.id,
            "version": f.version,
            "filename": f.filename,
            "sha256_hash": f.sha256_hash,
            "file_size": f.file_size,
            "created_at": f.created_at,
        }
        for f in firmware_list
    ]


async def async_get_ota_status(db: AsyncSession) -> dict:
    """Get OTA deployment status from DB.

    Returns: {deployments: [...], total, success_count,
              failed_count, in_progress_count}
    """
    result = await db.execute(
        select(OtaDeployment).order_by(OtaDeployment.created_at.desc())
    )
    deployments = result.scalars().all()

    dep_list = []
    for d in deployments:
        dep_list.append({
            "id": d.id,
            "firmware_id": d.firmware_id,
            "device_id": d.device_id,
            "status": d.status.value,
            "retry_count": d.retry_count,
            "error_message": d.error_message or "",
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        })

    success_count = sum(1 for d in deployments if d.status == OtaStatus.success)
    failed_count = sum(
        1 for d in deployments if d.status in (
            OtaStatus.failed, OtaStatus.hash_mismatch, OtaStatus.rolled_back
        )
    )
    in_progress_count = sum(
        1 for d in deployments if d.status in (
            OtaStatus.pending, OtaStatus.downloading,
            OtaStatus.applying, OtaStatus.verifying
        )
    )

    return {
        "deployments": dep_list,
        "total": len(deployments),
        "success_count": success_count,
        "failed_count": failed_count,
        "in_progress_count": in_progress_count,
    }


# ---------------------------------------------------------------------------
# Async versions of the business-logic functions
# ---------------------------------------------------------------------------

async def async_suggest_device_groups(db: AsyncSession, min_group_size: int = 3) -> dict:
    """Async version of suggest_device_groups using DB."""
    data = await async_list_devices(db)
    devices = data.get("devices", [])

    if not devices:
        return {"groups": [], "message": "No devices registered."}

    groups = []

    fw_groups: dict[str, list[str]] = {}
    for d in devices:
        fw = d.get("firmware_version", "unknown")
        fw_groups.setdefault(fw, []).append(d["id"])

    for fw, ids in fw_groups.items():
        if len(ids) >= min_group_size:
            groups.append({
                "name": f"Firmware {fw} Cohort",
                "dimension": "firmware_version",
                "value": fw,
                "device_ids": ids,
                "count": len(ids),
                "rationale": f"All {len(ids)} devices on firmware {fw}. "
                             f"OTA targeting or health comparison.",
            })

    signal_buckets = {"good": [], "moderate": [], "poor": []}
    for d in devices:
        sig = d.get("signal_strength", 0)
        if sig >= -60:
            signal_buckets["good"].append(d["id"])
        elif sig >= -80:
            signal_buckets["moderate"].append(d["id"])
        else:
            signal_buckets["poor"].append(d["id"])

    for bucket, ids in signal_buckets.items():
        if len(ids) >= min_group_size:
            groups.append({
                "name": f"Signal: {bucket.title()}",
                "dimension": "signal_strength",
                "value": bucket,
                "device_ids": ids,
                "count": len(ids),
                "rationale": f"{len(ids)} devices with {bucket} signal "
                             f"quality. May indicate regional coverage issue.",
            })

    return {"groups": groups, "total_devices": len(devices)}


async def async_detect_anomalies(db: AsyncSession) -> list[dict]:
    """Async version of detect_anomalies using DB."""
    anomalies = []
    now = _utcnow()

    devices_data = await async_list_devices(db)
    devices = devices_data.get("devices", [])

    ota_data = await async_get_ota_status(db)
    deployments = ota_data.get("deployments", [])

    ts = now.isoformat()

    weak_devices = [
        d for d in devices
        if d.get("signal_strength", 0) < -90 and d.get("status") == "online"
    ]
    if weak_devices:
        anomalies.append({
            "type": "weak_signal",
            "severity": "warning",
            "message": f"{len(weak_devices)} devices have critically weak "
                       f"signal (< -90 dBm). Possible hardware or placement issue.",
            "affected_device_ids": [d["id"] for d in weak_devices],
            "timestamp": ts,
        })

    stuck = [
        d for d in deployments
        if d.get("status") in ("downloading", "applying", "verifying")
    ]
    if stuck:
        anomalies.append({
            "type": "stuck_ota",
            "severity": "critical",
            "message": f"{len(stuck)} OTA deployments are stuck in "
                       f"non-terminal state. May indicate device communication failure.",
            "affected_device_ids": [d["device_id"] for d in stuck],
            "timestamp": ts,
        })

    if ota_data.get("total", 0) >= 5:
        fail_rate = ota_data.get("failed_count", 0) / max(ota_data["total"], 1)
        if fail_rate > 0.3:
            anomalies.append({
                "type": "ota_failure_spike",
                "severity": "critical",
                "message": f"OTA failure rate is {fail_rate:.0%} "
                           f"({ota_data['failed_count']}/{ota_data['total']}). "
                           f"Investigate firmware or deployment strategy.",
                "affected_device_ids": [],
                "timestamp": ts,
            })

    offline = [d for d in devices if d.get("status") == "offline"]
    if len(offline) > len(devices) * 0.3:
        anomalies.append({
            "type": "mass_offline",
            "severity": "critical",
            "message": f"{len(offline)} devices ({len(offline)/max(len(devices),1):.0%}) "
                       f"are offline. Possible network or backend issue.",
            "affected_device_ids": [d["id"] for d in offline],
            "timestamp": ts,
        })

    return anomalies


async def async_plan_ota_campaign(db: AsyncSession, firmware_version: str) -> dict:
    """Async version of plan_ota_campaign using DB."""
    devices_data = await async_list_devices(db, status="online")
    devices = devices_data.get("devices", [])

    firmware_list = await async_list_firmware(db)
    target_fw = next(
        (f for f in firmware_list if f["version"] == firmware_version),
        None,
    )
    if not target_fw:
        return {
            "error": f"Firmware version '{firmware_version}' not found. "
                     f"Upload it first via /ota/upload.",
            "available_firmware": [f["version"] for f in firmware_list],
        }

    if not devices:
        return {"error": "No online devices to target."}

    canary_size = max(1, len(devices) // 10)
    canary = devices[:canary_size]
    remainder = devices[canary_size:]

    phases = []
    remaining = list(remainder)
    for pct, label in [(30, "Phase 1"), (60, "Phase 2"), (100, "Phase 3")]:
        batch_size = max(0, int(len(devices) * pct / 100) - canary_size)
        batch = remaining[:batch_size]
        remaining = remaining[batch_size:]
        phases.append({
            "phase": label,
            "device_count": len(batch),
            "device_ids": [d["id"] for d in batch],
            "gate": f"Wait {3 * len(phases) + 5} min, verify "
                    f"failure rate < 20% before proceeding"
                    if phases else "No gate (final phase)",
        })
        if not remaining:
            break

    return {
        "firmware": {"id": target_fw["id"], "version": target_fw["version"]},
        "total_online_devices": len(devices),
        "canary_group": {
            "device_count": len(canary),
            "device_ids": [d["id"] for d in canary],
            "monitor_duration_seconds": 120,
            "pass_criteria": "failure_rate < 20% AND no critical anomalies",
        },
        "rollout_phases": phases,
        "risk_assessment": {
            "level": "low" if len(devices) < 50 else "medium",
            "note": f"{len(devices)} devices on mixed firmware versions. "
                    f"Canary group represents {len(canary)} devices.",
        },
        "recommendation": (
            f"Deploy firmware {firmware_version} to {len(canary)} canary "
            f"devices first. Monitor for 120s. If healthy, proceed "
            f"through {len(phases)} rollout phases. "
            f"Estimated completion: ~15 minutes."
        ),
    }
