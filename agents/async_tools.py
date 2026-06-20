"""
Fleet Commander — Async Database Tools for In-Backend Agent Usage

These tools query the database directly via SQLAlchemy instead of
making HTTP calls to the backend API. This avoids the self-referencing
deadlock when agents run inside the backend container.

Usage:
  from agents.async_tools import async_list_devices, async_list_firmware, ...
  from app.database import get_db
"""

import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DeviceStatus, Firmware, OtaDeployment, OtaStatus, V2gSchedule
from app.utils import utcnow

logger = logging.getLogger(__name__)


async def async_list_devices(db: AsyncSession, status: str | None = None) -> dict:
    """Fetch all devices from DB, optionally filtered by status.

    Returns: {devices: [...], total: N}  (same format as HTTP tools)
    """
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
                "mqtt_client_id": d.mqtt_client_id or "",
                "latitude": d.latitude,
                "longitude": d.longitude,
                "soc": d.soc,
                "soh": d.soh,
                "battery_temp": d.battery_temp,
                "plug_status": d.plug_status or "disconnected",
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
    now = utcnow()

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

    # ── 5. Individual device offline (new) ──
    for d in devices:
        if d.get("status") == "offline":
            last_seen = d.get("last_seen")
            if last_seen:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(last_seen)
                    elapsed = (now - dt).total_seconds()
                    if elapsed > 300:  # 5 minutes
                        anomalies.append({
                            "type": "device_offline",
                            "severity": "warning",
                            "message": f"Device '{d['name']}' has been offline for {int(elapsed//60)} minutes.",
                            "affected_device_ids": [d["id"]],
                            "timestamp": ts,
                        })
                except Exception:
                    pass

    # ── 6. V2G revenue drop (new) ──
    v2g_schedules = await async_list_v2g_schedules(db)
    total_revenue = sum(
        s.get("projected_revenue_dollars", 0.0)
        for s in v2g_schedules.get("schedules", [])
    )
    if total_revenue < 0 and len(devices) > 0:
        anomalies.append({
            "type": "v2g_revenue_drop",
            "severity": "warning",
            "message": f"V2G projected revenue is negative (${total_revenue:.2f}). "
                       f"Check spot prices and degradation costs.",
            "affected_device_ids": [],
            "timestamp": ts,
        })

    return anomalies


# ---------------------------------------------------------------------------
# Alert processing
# ---------------------------------------------------------------------------

async def async_process_anomalies(db: AsyncSession, anomalies: list[dict]) -> list[dict]:
    """Run anomalies through the alert engine: dedup, persist, notify.

    Returns processed alerts with status and IDs.
    """
    from app.alert_engine import AlertEngine
    engine = AlertEngine(db)
    return await engine.process_anomalies(anomalies)


async def async_get_alerts(db: AsyncSession, status: str = None, severity: str = None) -> dict:
    """Fetch alerts from DB.

    Returns: {alerts: [...], total: N}
    """
    from app.alert_engine import AlertEngine
    engine = AlertEngine(db)
    history = await engine.get_alert_history(status=status, severity=severity, limit=100)
    return history


async def async_acknowledge_alert(db: AsyncSession, alert_id: str, user: str) -> dict:
    """Acknowledge an alert.

    Returns: {success: bool, message: str}
    """
    from app.alert_engine import AlertEngine
    engine = AlertEngine(db)
    ok = await engine.acknowledge_alert(alert_id, user)
    return {"success": ok, "message": "Alert acknowledged" if ok else "Alert not found"}


async def async_resolve_alert(db: AsyncSession, alert_id: str) -> dict:
    """Resolve an alert.

    Returns: {success: bool, message: str}
    """
    from app.alert_engine import AlertEngine
    engine = AlertEngine(db)
    ok = await engine.resolve_alert(alert_id)
    return {"success": ok, "message": "Alert resolved" if ok else "Alert not found"}


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


async def async_onboard_device(
    db: AsyncSession,
    name: str,
    firmware_version: str = "",
    ip_address: str = "",
    mqtt_client_id: str = "",
    auto_register: bool = False,
) -> dict:
    """Check conflicts, recommend firmware, optionally register a new device.

    Returns an onboarding report with conflicts, recommended firmware,
    suggested initial config, and (if auto_register) the created device.
    """
    conflicts = []
    devices_data = await async_list_devices(db)
    existing_devices = devices_data.get("devices", [])

    for d in existing_devices:
        if d["name"].lower() == name.lower():
            conflicts.append({
                "type": "name",
                "existing_device_id": d["id"],
                "message": f"Device name '{name}' is already used by device {d['id'][:8]}...",
            })
        if mqtt_client_id and d.get("mqtt_client_id") == mqtt_client_id:
            conflicts.append({
                "type": "mqtt_client_id",
                "existing_device_id": d["id"],
                "message": f"MQTT client ID '{mqtt_client_id}' is already assigned to device {d['id'][:8]}...",
            })

    fw_list = await async_list_firmware(db)
    recommended_fw = None
    if firmware_version:
        recommended_fw = next((f for f in fw_list if f["version"] == firmware_version), None)
        if not recommended_fw and fw_list:
            recommended_fw = fw_list[0]
    elif fw_list:
        recommended_fw = fw_list[0]

    initial_config = {
        "heartbeat_interval_seconds": 10,
        "ota_poll_interval_seconds": 60,
        "log_level": "INFO",
    }

    device = None
    registration_status = "skipped"
    if auto_register and not conflicts:
        new_device = Device(
            name=name,
            firmware_version=recommended_fw["version"] if recommended_fw else "1.0.0",
            status=DeviceStatus.online,
            last_seen=utcnow(),
            ip_address=ip_address or "",
            mqtt_client_id=mqtt_client_id or None,
        )
        db.add(new_device)
        await db.commit()
        await db.refresh(new_device)
        device = {
            "id": new_device.id,
            "name": new_device.name,
            "firmware_version": new_device.firmware_version,
            "status": new_device.status.value,
            "mqtt_client_id": new_device.mqtt_client_id or "",
            "ip_address": new_device.ip_address or "",
            "last_seen": new_device.last_seen.isoformat() if new_device.last_seen else None,
        }
        registration_status = "created"

    online_count = sum(1 for d in existing_devices if d.get("status") == "online")

    return {
        "onboarding_possible": len(conflicts) == 0,
        "conflicts": conflicts,
        "recommended_firmware": recommended_fw,
        "initial_config": initial_config,
        "device": device,
        "registration_status": registration_status,
        "fleet_state": {
            "total_devices": devices_data.get("total", 0),
            "online_devices": online_count,
        },
    }


# ---------------------------------------------------------------------------
# Aegis remediation tools (Sprint 2)
# ---------------------------------------------------------------------------


async def async_detect_resource_pressure(db: AsyncSession) -> dict:
    """Check for resource pressure signals from Prometheus metrics.

    Returns: {pressure_detected: bool, signals: [...], metrics_summary: str}
    """
    from app.aegis.engine import AegisEngine
    engine = AegisEngine()
    metrics_text = await engine._scrape_metrics()
    if not metrics_text:
        return {"pressure_detected": False, "signals": [], "metrics_summary": "No metrics available"}

    signals = engine._classify_metrics(metrics_text)
    return {
        "pressure_detected": len(signals) > 0,
        "signals": [s.model_dump() for s in signals],
        "metrics_summary": f"{len(signals)} signal(s) detected" if signals else "All metrics within normal range",
    }


async def async_run_remediation_cycle(db: AsyncSession) -> dict:
    """Execute one full Aegis remediation cycle: scrape -> classify -> decide -> act.

    Returns: {cycle_completed: bool, remediations_created: int, summary: str}
    """
    from app.aegis.engine import AegisEngine
    engine = AegisEngine()
    await engine.run_cycle(db)
    from app.aegis.models import Remediation
    from sqlalchemy import select, func
    result = await db.execute(select(func.count()).select_from(Remediation))
    total = result.scalar() or 0
    return {
        "cycle_completed": True,
        "remediations_created": total,
        "summary": f"Remediation cycle completed. Total remediations: {total}",
    }


async def async_get_remediation_history(
    db: AsyncSession,
    status: str = None,
    action: str = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Fetch remediation history from DB.

    Returns: {remediations: [...], total: N, limit, offset}
    """
    from app.aegis.models import Remediation
    query = select(Remediation)
    count_query = select(func.count()).select_from(Remediation)

    if status:
        query = query.where(Remediation.status == status)
        count_query = count_query.where(Remediation.status == status)
    if action:
        query = query.where(Remediation.action_name == action)
        count_query = count_query.where(Remediation.action_name == action)

    query = query.order_by(Remediation.started_at.desc())

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.offset(offset).limit(limit))
    remediations = result.scalars().all()

    return {
        "remediations": [
            {
                "id": r.id,
                "signal_id": r.signal_id,
                "metric_name": r.metric_name,
                "value": r.value,
                "threshold": r.threshold,
                "severity": r.severity,
                "rule_name": r.rule_name,
                "action_name": r.action_name,
                "status": r.status,
                "error_message": r.error_message,
                "input_snapshot": r.input_snapshot,
                "output_snapshot": r.output_snapshot,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "duration_ms": r.duration_ms,
                "device_ids": r.device_ids,
            }
            for r in remediations
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def async_list_v2g_schedules(db: AsyncSession) -> dict:
    """Fetch active V2G schedules from DB.

    Returns: {schedules: [...], total: N}
    """
    result = await db.execute(
        select(V2gSchedule).order_by(V2gSchedule.created_at.desc())
    )
    schedules = result.scalars().all()
    return {
        "schedules": [
            {
                "id": s.id,
                "device_id": s.device_id,
                "action": s.action.value,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
                "power_kw": s.power_kw,
                "energy_kwh": s.energy_kwh,
                "spot_price_per_kwh": s.spot_price_per_kwh,
                "deg_cost_per_kwh": s.deg_cost_per_kwh,
                "projected_revenue_dollars": s.projected_revenue_dollars,
            }
            for s in schedules
        ],
        "total": len(schedules),
    }
