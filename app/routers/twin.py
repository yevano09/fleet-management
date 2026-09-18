"""
Fleet Commander — Merged Digital Twin view (MVP TWIN-01)

GET /twin/{device_id} joins everything the platform knows about one asset
into a single "asset page" API: identity + lifecycle, versioned shadow
(desired/reported + sync), open ML/legacy risks, active alerts, relevant OTA
schedules, recent V2G dispatches, and a 0-100 health score.

Health score (documented, deterministic):
    start 100
    -25 per unresolved HIGH risk (>= 0.7), cap -50
    -15 per unresolved MEDIUM risk (>= 0.4), cap -30
    -20 if device is offline
    -10 per active alert touching the device, cap -30
    floor 0
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_user
from app.models import (
    Alert,
    AlertStatus,
    Device,
    DeviceStatus,
    DeviceShadow,
    OtaSchedule,
    PredictedFailure,
    ScheduleStatus,
    Telemetry,
    V2gSchedule,
)
from app.routers.shadow import _is_in_sync, _shadow_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twin", tags=["twin"])


def health_score(risks: list, offline: bool, active_alerts: int) -> int:
    high = sum(1 for r in risks if (r.risk_score or 0) >= 0.7)
    med = sum(1 for r in risks if 0.4 <= (r.risk_score or 0) < 0.7)
    score = 100 - min(50, 25 * high) - min(30, 15 * med)
    if offline:
        score -= 20
    score -= min(30, 10 * active_alerts)
    return max(0, score)


@router.get("/{device_id}")
async def get_twin(
    device_id: str,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    desired_res = await db.execute(
        select(DeviceShadow)
        .where(DeviceShadow.device_id == device_id, DeviceShadow.state == "desired")
        .order_by(DeviceShadow.version.desc()).limit(1)
    )
    reported_res = await db.execute(
        select(DeviceShadow)
        .where(DeviceShadow.device_id == device_id, DeviceShadow.state == "reported")
        .order_by(DeviceShadow.version.desc()).limit(1)
    )
    desired = desired_res.scalar_one_or_none()
    reported = reported_res.scalar_one_or_none()

    risks_res = await db.execute(
        select(PredictedFailure)
        .where(PredictedFailure.device_id == device_id, PredictedFailure.resolved == False)  # noqa: E712
        .order_by(PredictedFailure.risk_score.desc()).limit(5)
    )
    risks = risks_res.scalars().all()

    alerts_res = await db.execute(
        select(Alert).where(
            Alert.status.in_([AlertStatus.active, AlertStatus.acknowledged]),
            Alert.device_ids.like(f"%{device_id}%"),
        )
    )
    alerts = alerts_res.scalars().all()

    sched_res = await db.execute(
        select(OtaSchedule).where(
            OtaSchedule.status.in_([ScheduleStatus.scheduled, ScheduleStatus.running])
        )
    )
    schedules = [
        s for s in sched_res.scalars().all()
        if s.all_devices or (s.device_ids and device_id in s.device_ids.split(","))
    ]

    v2g_res = await db.execute(
        select(V2gSchedule).where(V2gSchedule.device_id == device_id)
        .order_by(V2gSchedule.start_time.desc()).limit(5)
    )
    v2g_rows = v2g_res.scalars().all()

    tel_res = await db.execute(
        select(Telemetry).where(Telemetry.device_id == device_id)
        .order_by(Telemetry.timestamp.desc()).limit(20)
    )
    tel_rows = tel_res.scalars().all()
    latest_tel = tel_rows[0] if tel_rows else None

    def _first(field):
        for row in tel_rows:
            val = getattr(row, field, None)
            if val is not None and val != "[]":
                return val
        return None

    offline = device.status != DeviceStatus.online
    score = health_score(risks, offline, len(alerts))

    def _dtcs(row):
        if not row or not row.dtc_codes:
            return []
        try:
            import json as _json

            val = _json.loads(row.dtc_codes)
            return val if isinstance(val, list) else []
        except Exception:
            return []

    return {
        "device_id": device.id,
        "name": device.name,
        "firmware_version": device.firmware_version,
        "status": device.status.value if hasattr(device.status, "value") else device.status,
        "lifecycle_status": (
            device.lifecycle_status.value
            if hasattr(device.lifecycle_status, "value") else device.lifecycle_status
        ),
        "city": device.city,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "health_score": score,
        "vehicle": {
            "vin": device.vin,
            "make": device.make,
            "model": device.model,
            "model_year": device.model_year,
            "odometer_km": _first("odometer_km"),
            "fuel_level_pct": _first("fuel_level_pct"),
            "dtc_codes": _dtcs(next((r for r in tel_rows if r.dtc_codes and r.dtc_codes != "[]"), None)),
            "cell_min_v": _first("cell_min_v"),
            "cell_max_v": _first("cell_max_v"),
            "cell_spread_mv": _first("cell_spread_mv"),
            "telemetry_at": latest_tel.timestamp.isoformat() if latest_tel and latest_tel.timestamp else None,
        },
        "shadow": {
            "desired": _shadow_to_dict(desired) if desired else None,
            "reported": _shadow_to_dict(reported) if reported else None,
            "in_sync": _is_in_sync(desired, reported),
        },
        "open_risks": [
            {
                "id": r.id,
                "risk_type": r.risk_type,
                "risk_score": r.risk_score,
                "confidence": r.confidence,
                "predicted_hours_to_failure": r.predicted_hours_to_failure,
                "model_version": r.model_version,
                "recommendation": r.recommendation,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in risks
        ],
        "active_alerts": [
            {"id": a.id, "type": a.type, "severity": a.severity,
             "message": a.message, "status": a.status.value,
             "work_order_id": a.work_order_id}
            for a in alerts
        ],
        "relevant_schedules": [
            {"id": s.id, "name": s.name,
             "status": s.status.value if hasattr(s.status, "value") else s.status,
             "scheduled_for": s.scheduled_for.isoformat() if s.scheduled_for else None}
            for s in schedules
        ],
        "recent_v2g": [
            {"action": v.action.value if hasattr(v.action, "value") else v.action,
             "power_kw": v.power_kw,
             "projected_revenue": v.projected_revenue_dollars,
             "start_time": v.start_time.isoformat() if v.start_time else None}
            for v in v2g_rows
        ],
        "last_sync": (
            reported.timestamp.isoformat()
            if reported and reported.timestamp
            else (device.last_seen.isoformat() if device.last_seen else None)
        ),
    }
