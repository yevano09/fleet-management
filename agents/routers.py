"""
Fleet Commander — Agent Recommendations API Router

Exposes Phase 1 agent outputs as REST endpoints consumed by the dashboard.
Uses async database-backed tools for in-backend execution (no self-referencing HTTP).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings as app_settings
from agents.async_tools import (
    async_plan_ota_campaign,
    async_detect_anomalies,
    async_suggest_device_groups,
    async_list_devices,
    async_list_firmware,
    async_list_v2g_schedules,
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

    if notify and critical:
        for ca in critical:
            http_tools.send_slack_alert(ca["message"], severity="critical")
    if notify and warnings:
        for wa in warnings:
            http_tools.send_slack_alert(wa["message"], severity="warning")

    return {
        "agent": "Fleet Health Monitor",
        "type": "anomaly_check",
        "status": "anomalies_found",
        "summary": (
            f"Found {len(critical)} critical and {len(warnings)} "
            f"warning anomalies."
        ),
        "anomalies": anomalies,
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
    except Exception as e:
        logger.exception("OTA agent failed")
        results.append({"agent": "OTA Campaign Strategist", "type": "ota_campaign",
                        "error": str(e)})

    try:
        results.append(await _run_anomaly_agent(db, notify=notify))
    except Exception as e:
        logger.exception("Anomaly agent failed")
        results.append({"agent": "Fleet Health Monitor", "type": "anomaly_check",
                        "error": str(e)})

    try:
        results.append(await _run_group_agent(db, min_group_size=min_group_size))
    except Exception as e:
        logger.exception("Group agent failed")
        results.append({"agent": "Device Group Manager", "type": "device_groups",
                        "error": str(e)})

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

    spot_prices = mock_spot_prices(hours=horizon_hours)

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

    # Publish V2G commands for non-idle slots (first device only for demo)
    if full_schedule and target_devices:
        first_device = target_devices[0]
        for slot in full_schedule[:6]:  # first 6 slots
            if slot.action in ("charge", "discharge"):
                mqtt_client.publish_v2g_command(
                    device_id=first_device["id"],
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
