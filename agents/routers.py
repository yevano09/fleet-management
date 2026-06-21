"""
Fleet Commander — Agent Recommendations API Router

Exposes Phase 1 agent outputs as REST endpoints consumed by the dashboard.
Uses async database-backed tools for in-backend execution (no self-referencing HTTP).
"""

from __future__ import annotations

import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils import utcnow
from app.config import settings as app_settings
from agents.async_tools import (
    async_plan_ota_campaign,
    async_detect_anomalies,
    async_suggest_device_groups,
    async_onboard_device,
    async_list_devices,
    async_list_firmware,
    async_list_v2g_schedules,
    async_process_anomalies,
)
from app.v2g_optimizer import heuristic_optimize, mock_spot_prices, degradation_cost_per_kwh, DegradationParams
from app.schemas import V2gDispatchResponse, V2gDispatchSlot
from app.metrics import v2g_projected_revenue_dollars, battery_degradation_cost_dollars
from app.mqtt_client import mqtt_client
from agents import tools as http_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


async def _run_ota_agent(db: AsyncSession,
                         firmware_version: Optional[str] = None) -> dict:
    """Async OTA campaign agent using DB access."""
    if firmware_version:
        plan = await async_plan_ota_campaign(db, firmware_version)
    else:
        fw_list = await async_list_firmware(db)
        if not fw_list:
            return {"error": "No firmware uploaded. Upload firmware first."}
        plan = await async_plan_ota_campaign(db, fw_list[0]["version"])

    if "error" in plan:
        return plan

    return {
        "agent": "OTA Campaign Strategist",
        "type": "ota_campaign",
        "summary": plan["recommendation"],
        "details": {
            "firmware": plan["firmware"],
            "total_online_devices": plan["total_online_devices"],
            "canary_group": plan["canary_group"],
            "rollout_phases": plan["rollout_phases"],
            "risk_assessment": plan["risk_assessment"],
        },
        "human_input_required": True,
    }


async def _run_anomaly_agent(db: AsyncSession, notify: bool = True) -> dict:
    """Async anomaly detection agent using DB access."""
    anomalies = await async_detect_anomalies(db)

    if not anomalies:
        return {
            "agent": "Fleet Health Monitor",
            "type": "anomaly_check",
            "status": "healthy",
            "summary": "No anomalies detected. Fleet is healthy.",
            "anomalies": [],
        }

    critical = [a for a in anomalies if a["severity"] == "critical"]
    warnings = [a for a in anomalies if a["severity"] == "warning"]

    processed = []
    if notify:
        processed = await async_process_anomalies(db, anomalies)

    return {
        "agent": "Fleet Health Monitor",
        "type": "anomaly_check",
        "status": "anomalies_found",
        "summary": (
            f"Found {len(critical)} critical and {len(warnings)} "
            f"warning anomalies."
        ),
        "anomalies": anomalies,
        "processed": processed,
        "notifications_sent": notify,
    }


async def _run_group_agent(db: AsyncSession, min_group_size: int = 3) -> dict:
    """Async device group agent using DB access."""
    result = await async_suggest_device_groups(db, min_group_size=min_group_size)
    groups = result.get("groups", [])

    if not groups:
        return {
            "agent": "Device Group Manager",
            "type": "device_groups",
            "summary": "No meaningful groups found. Fewer devices than min_group_size.",
            "groups": [],
            "total_devices": result.get("total_devices", 0),
        }

    return {
        "agent": "Device Group Manager",
        "type": "device_groups",
        "summary": f"Found {len(groups)} device groups for targeted management.",
        "groups": groups,
        "total_devices": result.get("total_devices", 0),
        "human_input_required": True,
    }


@router.get("/recommendations")
async def get_all_recommendations(
    notify: bool = Query(True, description="Send Slack alerts for critical anomalies"),
    firmware_version: Optional[str] = Query(None, description="Target firmware version for OTA"),
    min_group_size: int = Query(3, ge=1, description="Minimum devices per group"),
    db: AsyncSession = Depends(get_db),
):
    """Run all three Phase 1 agents and return their recommendations."""
    results = []

    try:
        results.append(await _run_ota_agent(db, firmware_version))
    except Exception:
        logger.exception("OTA agent failed")
        results.append({"agent": "OTA Campaign Strategist", "type": "ota_campaign",
                        "error": "Internal error"})

    try:
        results.append(await _run_anomaly_agent(db, notify=notify))
    except Exception:
        logger.exception("Anomaly agent failed")
        results.append({"agent": "Fleet Health Monitor", "type": "anomaly_check",
                        "error": "Internal error"})

    try:
        results.append(await _run_group_agent(db, min_group_size=min_group_size))
    except Exception:
        logger.exception("Group agent failed")
        results.append({"agent": "Device Group Manager", "type": "device_groups",
                        "error": "Internal error"})

    return {"agents": results}


@router.get("/ota-campaign")
async def get_ota_recommendation(
    firmware_version: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Run the OTA Campaign agent: suggests canary rollout plan."""
    result = await _run_ota_agent(db, firmware_version)
    return result


@router.get("/anomaly-check")
async def get_anomaly_check(
    notify: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Run the Fleet Health agent: detect anomalies and optionally alert."""
    result = await _run_anomaly_agent(db, notify=notify)
    return result


@router.get("/fleet-health")
async def get_fleet_health(
    db: AsyncSession = Depends(get_db),
):
    """Run anomaly detection and process through the alert engine.

    This endpoint always fires alerts (notify=True) unlike /anomaly-check
    which is read-only by default.
    """
    result = await _run_anomaly_agent(db, notify=True)
    return result


@router.get("/device-groups")
async def get_device_groups(
    min_group_size: int = Query(3, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Run the Device Group Manager agent: suggest device groupings."""
    result = await _run_group_agent(db, min_group_size=min_group_size)
    return result


@router.get("/v2g-dispatch", response_model=V2gDispatchResponse)
async def get_v2g_dispatch(
    db: AsyncSession = Depends(get_db),
    device_ids: Optional[list[str]] = Query(None, description="Filter to specific device IDs"),
    all_devices: bool = Query(False, description="Include all devices"),
    horizon_hours: int = Query(24, description="Optimization horizon in hours"),
):
    """Run the V2G Arbitrage Optimizer agent.

    Produces a charge/discharge schedule for the fleet maximizing
    net revenue (spot price - degradation cost).
    """
    devices_data = await async_list_devices(db)
    all_devices_list = devices_data.get("devices", [])

    if device_ids:
        target_devices = [d for d in all_devices_list if d["id"] in device_ids]
    else:
        target_devices = all_devices_list

    if not target_devices:
        return V2gDispatchResponse(
            summary="No devices available for V2G dispatch.",
            total_projected_revenue_dollars=0.0,
            total_deg_cost_dollars=0.0,
            schedule=[],
            devices_used=0,
        )

    # Feature 10: use real spot prices when configured, mock otherwise
    from app.spot_prices import fetch_spot_prices
    spot_prices = fetch_spot_prices(hours=horizon_hours)

    full_schedule: list[V2gDispatchSlot] = []
    total_revenue = 0.0
    total_deg = 0.0
    devices_used = 0

    for device in target_devices:
        soc = device.get("soc", 80.0)
        soh = device.get("soh", 100.0)
        battery_temp = device.get("battery_temp", 25.0)
        plug_status = device.get("plug_status", "disconnected")

        schedule, rev, deg = heuristic_optimize(
            soc_current=soc,
            soh=soh,
            battery_temp=battery_temp,
            plug_status=plug_status,
            horizon_hours=horizon_hours,
            spot_prices=spot_prices,
        )
        if schedule:
            devices_used += 1
            total_revenue += rev
            total_deg += deg
            for slot in schedule:
                full_schedule.append(V2gDispatchSlot(
                    start_time=slot.start_time,
                    end_time=slot.end_time,
                    action=slot.action,
                    power_kw=slot.power_kw,
                    energy_kwh=slot.energy_kwh,
                    spot_price_per_kwh=slot.spot_price_per_kwh,
                    deg_cost_per_kwh=slot.deg_cost_per_kwh,
                    net_revenue_dollars=slot.net_revenue_dollars,
                ))

    # Publish V2G commands for non-idle slots across all target devices
    if full_schedule and target_devices:
        for device in target_devices:
            device_schedule = [s for s in full_schedule if s.action in ("charge", "discharge")][:6]
            for slot in device_schedule:
                mqtt_client.publish_v2g_command(
                    device_id=device["id"],
                    action=slot.action,
                    power_kw=slot.power_kw,
                    duration_minutes=60,
                )

    v2g_projected_revenue_dollars.set(total_revenue)
    battery_degradation_cost_dollars.set(total_deg)

    summary = (
        f"V2G arbitrage schedule generated for {devices_used} device(s). "
        f"Projected revenue: ${total_revenue:.2f}, "
        f"degradation cost: ${total_deg:.2f}, "
        f"net: ${total_revenue - total_deg:.2f}."
    )

    return V2gDispatchResponse(
        summary=summary,
        total_projected_revenue_dollars=total_revenue,
        total_deg_cost_dollars=total_deg,
        schedule=full_schedule,
        devices_used=devices_used,
    )


# ---------------------------------------------------------------------------
# Device Onboarding Agent
# ---------------------------------------------------------------------------

async def _run_onboarding_agent(
    db: AsyncSession,
    name: str,
    firmware_version: str = "",
    ip_address: str = "",
    mqtt_client_id: str = "",
    auto_register: bool = False,
) -> dict:
    """Async device onboarding agent using DB access."""
    if not name:
        return {"error": "Device name is required for onboarding."}

    plan = await async_onboard_device(
        db,
        name=name,
        firmware_version=firmware_version,
        ip_address=ip_address,
        mqtt_client_id=mqtt_client_id,
        auto_register=auto_register,
    )

    if not plan.get("onboarding_possible"):
        return {
            "agent": "Device Onboarding Agent",
            "type": "device_onboarding",
            "summary": f"Cannot onboard '{name}' due to conflicts.",
            "details": {
                "onboarding_possible": False,
                "conflicts": plan.get("conflicts", []),
                "recommended_firmware": plan.get("recommended_firmware"),
                "initial_config": plan.get("initial_config"),
                "fleet_state": plan.get("fleet_state"),
            },
        }

    device = plan.get("device")
    registration_status = plan.get("registration_status", "skipped")
    mqtt_config_pushed = False
    verification_status = "pending"

    if auto_register and device:
        mqtt_client.publish_remote_config(
            device_id=device["id"],
            config=plan["initial_config"],
        )
        mqtt_config_pushed = True

        devices_after = await async_list_devices(db)
        for d in devices_after.get("devices", []):
            if d["id"] == device["id"] and d.get("status") == "online":
                verification_status = "verified"
                break

    return {
        "agent": "Device Onboarding Agent",
        "type": "device_onboarding",
        "summary": (
            f"Device '{name}' onboarded successfully."
            if registration_status == "created"
            else f"Onboarding plan for '{name}' ready for review."
        ),
        "human_input_required": not auto_register,
        "details": {
            "onboarding_possible": plan["onboarding_possible"],
            "conflicts": plan.get("conflicts", []),
            "recommended_firmware": plan["recommended_firmware"],
            "initial_config": plan.get("initial_config"),
            "device": device,
            "registration_status": registration_status,
            "verification_status": verification_status,
            "mqtt_config_pushed": mqtt_config_pushed,
            "fleet_state": plan.get("fleet_state"),
        },
    }


@router.get("/onboarding")
async def get_onboarding_recommendation(
    name: str = Query(..., description="Device name to onboard"),
    firmware_version: Optional[str] = Query(None, description="Firmware version to assign"),
    ip_address: str = Query("", description="Device IP address"),
    mqtt_client_id: Optional[str] = Query(None, description="MQTT client identifier"),
    auto_register: bool = Query(False, description="Execute onboarding (register + push config)"),
    db: AsyncSession = Depends(get_db),
):
    """Run the Device Onboarding Agent: recommend or execute adding a new device to the fleet."""
    result = await _run_onboarding_agent(
        db,
        name=name,
        firmware_version=firmware_version or "",
        ip_address=ip_address,
        mqtt_client_id=mqtt_client_id or "",
        auto_register=auto_register,
    )
    return result


# ---------------------------------------------------------------------------
# Aegis Remediation Agent (Sprint 2)
# ---------------------------------------------------------------------------

async def _run_remediation_agent(db: AsyncSession) -> dict:
    """Async Aegis remediation agent using DB access."""
    from agents.async_tools import async_detect_resource_pressure, async_run_remediation_cycle, async_get_remediation_history

    pressure = await async_detect_resource_pressure(db)
    if pressure.get("pressure_detected"):
        cycle_result = await async_run_remediation_cycle(db)
    else:
        cycle_result = {"cycle_completed": False, "remediations_created": 0}

    history = await async_get_remediation_history(db, limit=5)

    return {
        "agent": "Aegis Remediation Agent",
        "type": "remediation",
        "summary": (
            cycle_result.get("summary", "No cycle executed")
            if pressure.get("pressure_detected")
            else "No resource pressure detected"
        ),
        "details": {
            "pressure_detected": pressure.get("pressure_detected", False),
            "signals": pressure.get("signals", []),
            "metrics_summary": pressure.get("metrics_summary", ""),
            "cycle_completed": cycle_result.get("cycle_completed", False),
            "remediations_created": cycle_result.get("remediations_created", 0),
            "recent_remediations": history.get("remediations", []),
            "total_history": history.get("total", 0),
        },
    }


@router.get("/aegis/scan")
async def get_aegis_scan(
    db: AsyncSession = Depends(get_db),
):
    """Run the Aegis Remediation Agent: check pressure and run a cycle if needed."""
    result = await _run_remediation_agent(db)
    return result


@router.get("/aegis/history")
async def get_aegis_history(
    status: Optional[str] = Query(None, description="Filter by status"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Fetch Aegis remediation history via agent interface."""
    from agents.async_tools import async_get_remediation_history
    result = await async_get_remediation_history(
        db, status=status, action=action, limit=limit, offset=offset
    )
    return result


@router.post("/aegis/rerun/{remediation_id}")
async def rerun_remediation(
    remediation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Re-run a specific remediation action from history."""
    from app.aegis.models import Remediation
    from app.aegis.engine import get_engine
    from sqlalchemy import select

    result = await db.execute(select(Remediation).where(Remediation.id == remediation_id))
    remediation = result.scalar_one_or_none()
    if not remediation:
        raise HTTPException(status_code=404, detail="Remediation not found")

    from app.aegis.schemas import RemediationSignal
    from datetime import datetime
    signal = RemediationSignal(
        id=remediation.signal_id or str(uuid.uuid4()),
        metric_name=remediation.metric_name,
        value=remediation.value,
        threshold=remediation.threshold,
        severity=remediation.severity,
        timestamp=utcnow(),
        device_ids=remediation.device_ids.split(",") if remediation.device_ids else [],
        window_seconds=60,
    )

    engine = get_engine()
    rule = engine.registry.get_rule(remediation.rule_name) if remediation.rule_name else None
    if rule:
        await engine._execute_remediation(db, signal, rule)
        return {"success": True, "message": f"Remediation {remediation_id[:8]} re-run", "status": "completed"}
    else:
        return {"success": False, "message": f"Rule '{remediation.rule_name}' not found", "status": "failed"}


# ---------------------------------------------------------------------------
# Predictive Maintenance Agent (Feature 3)
# ---------------------------------------------------------------------------

@router.get("/predictive-scan")
async def get_predictive_scan(db: AsyncSession = Depends(get_db)):
    """Run the Predictive Maintenance Agent: analyze telemetry trends and predict failures."""
    from agents.async_tools import async_run_predictive_scan, async_get_predictions
    scan = await async_run_predictive_scan(db)
    predictions = scan.get("predictions", [])
    high_risk = [p for p in predictions if p["risk_score"] >= 0.7]
    medium_risk = [p for p in predictions if 0.4 <= p["risk_score"] < 0.7]

    return {
        "agent": "Predictive Maintenance Agent",
        "type": "predictive_maintenance",
        "summary": (
            f"Analyzed fleet telemetry. Found {len(high_risk)} high-risk and "
            f"{len(medium_risk)} medium-risk failure predictions."
            if predictions
            else "No failure risks detected. Fleet telemetry trends are healthy."
        ),
        "details": {
            "predictions_count": len(predictions),
            "high_risk_count": len(high_risk),
            "medium_risk_count": len(medium_risk),
            "predictions": predictions,
        },
    }


@router.get("/predictive-history")
async def get_predictive_history(
    min_risk: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Fetch active failure predictions."""
    from agents.async_tools import async_get_predictions
    result = await async_get_predictions(db, min_risk=min_risk, resolved=False, limit=limit)
    return result
