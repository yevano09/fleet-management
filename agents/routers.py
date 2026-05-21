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
)
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
