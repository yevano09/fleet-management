"""
Fleet Commander — Phase 1 Crew AI Agents

Three AI agent crews for the Assisted Phase:
  1. OTA Campaign Agent — Recommends canary-based rollout plans
  2. Anomaly Detection Agent — Detects fleet anomalies and alerts
  3. Device Group Manager — Suggests device groupings

Each crew can run in two modes:
  - LLM mode (with Crew AI + API key) for natural-language reasoning
  - Tool-only mode (heuristic logic) for standalone operation

Usage:
  from agents.phase1_crew import run_all_agents, run_ota_agent, run_anomaly_agent, run_group_agent
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from agents import tools

logger = logging.getLogger(__name__)

USE_CREWAI = os.environ.get("CREWAI_ENABLED", "").lower() in ("1", "true", "yes")

if USE_CREWAI:
    try:
        from crewai import Agent, Task, Crew, Process
    except ImportError:
        logger.warning("crewai not installed — falling back to tool-only mode")
        USE_CREWAI = False


# ---------------------------------------------------------------------------
# 1. OTA Campaign Agent
# ---------------------------------------------------------------------------

def run_ota_agent(firmware_version: Optional[str] = None) -> dict:
    """Plan an OTA rollout campaign with canary analysis.

    Returns recommendation with phases, gates, and risk assessment.
    """
    if firmware_version:
        plan = tools.plan_ota_campaign(firmware_version)
    else:
        fw_list = tools.list_firmware()
        if not fw_list:
            return {"error": "No firmware uploaded. Upload firmware first."}
        plan = tools.plan_ota_campaign(fw_list[0]["version"])

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


if USE_CREWAI:

    def run_ota_agent_llm(firmware_version: Optional[str] = None) -> dict:
        """Crew AI version of the OTA campaign agent."""
        fw_list = tools.list_firmware()
        target_fw = firmware_version or (fw_list[0]["version"] if fw_list else None)
        if not target_fw:
            return {"error": "No firmware available."}

        devices_data = tools.list_devices()
        devices = devices_data.get("devices", [])
        device_pool = [d["name"] for d in devices if d.get("status") == "online"]

        ota_strategist = Agent(
            role="OTA Campaign Strategist",
            goal="Plan safe, gradual OTA firmware rollouts with canary groups",
            backstory=(
                "You are an expert in firmware deployment strategies. "
                "You always use canary groups, phased rollouts, and gate checks "
                "to minimize risk."
            ),
            tools=[tools.list_devices, tools.list_firmware],
            allow_delegation=False,
            verbose=True,
        )

        plan_task = Task(
            description=(
                f"Firmware version: {target_fw}\n"
                f"Online device pool: {device_pool}\n\n"
                "1. Pick a 10% canary group from the online devices\n"
                "2. Design 3 rollout phases (30%, 60%, 100%)\n"
                "3. Define pass/fail gates for each phase\n"
                "4. Assess risk level\n"
                "Return a structured campaign plan."
            ),
            expected_output=(
                "Structured campaign plan with canary device IDs, "
                "3 rollout phases with counts and gate criteria, "
                "and risk assessment."
            ),
            agent=ota_strategist,
            human_input=True,
        )

        crew = Crew(
            agents=[ota_strategist],
            tasks=[plan_task],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff()
        return {
            "agent": "OTA Campaign Strategist (LLM)",
            "type": "ota_campaign",
            "summary": str(result),
            "human_input_required": True,
        }


# ---------------------------------------------------------------------------
# 2. Anomaly Detection Agent
# ---------------------------------------------------------------------------

def run_anomaly_agent(notify: bool = True) -> dict:
    """Run heuristic anomaly detection across the fleet.

    If notify=True, sends Slack alerts for critical anomalies.
    Returns all anomalies found.
    """
    anomalies = tools.detect_anomalies()

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
            tools.send_slack_alert(ca["message"], severity="critical")
            logger.info(f"Slack alert sent for critical anomaly: {ca['type']}")
    if notify and warnings:
        for wa in warnings:
            tools.send_slack_alert(wa["message"], severity="warning")

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


if USE_CREWAI:

    def run_anomaly_agent_llm(notify: bool = True) -> dict:
        """Crew AI version of the anomaly detection agent."""

        monitor = Agent(
            role="Fleet Health Monitor",
            goal="Detect and diagnose fleet anomalies in real-time",
            backstory=(
                "You are a vigilant fleet monitoring specialist. "
                "You watch device signals, OTA status, and metrics for "
                "anything unusual."
            ),
            tools=[tools.fetch_metrics, tools.list_devices, tools.get_ota_status],
            allow_delegation=False,
            verbose=True,
        )

        diagnose_task = Task(
            description=(
                "1. Fetch current fleet metrics\n"
                "2. List all devices and their statuses\n"
                "3. Check OTA deployment status\n"
                "4. Identify any anomalies (offline spikes, stuck OTAs, "
                "weak signals, failure rate > 30%)\n"
                "5. For each anomaly, rate severity: critical/warning/info\n"
                "Return a structured anomaly report."
            ),
            expected_output=(
                "Anomaly report with: number of anomalies, "
                "each with type, severity, affected devices, message, "
                "and recommended action."
            ),
            agent=monitor,
        )

        crew = Crew(
            agents=[monitor],
            tasks=[diagnose_task],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff()

        if notify:
            tools.send_slack_alert(str(result), severity="info")

        return {
            "agent": "Fleet Health Monitor (LLM)",
            "type": "anomaly_check",
            "status": "completed",
            "summary": str(result),
            "notifications_sent": notify,
        }


# ---------------------------------------------------------------------------
# 3. Device Group Manager
# ---------------------------------------------------------------------------

def run_group_agent(min_group_size: int = 3) -> dict:
    """Suggest device groupings based on firmware, signal strength, status.

    Returns structured groups with rationale for each.
    """
    result = tools.suggest_device_groups(min_group_size=min_group_size)
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


if USE_CREWAI:

    def run_group_agent_llm(min_group_size: int = 3) -> dict:
        """Crew AI version of the device group manager."""

        group_manager = Agent(
            role="Device Group Manager",
            goal="Suggest optimal device groupings for targeted operations",
            backstory=(
                "You organize IoT fleets into meaningful groups "
                "based on firmware versions, signal quality, location, "
                "and behavioral patterns."
            ),
            tools=[tools.list_devices],
            allow_delegation=False,
            verbose=True,
        )

        group_task = Task(
            description=(
                "1. Fetch all devices\n"
                "2. Group them by firmware version\n"
                "3. Group them by signal strength (good >= -60, "
                "moderate -60 to -80, poor < -80)\n"
                "4. Suggest which grouping is most useful for OTA targeting\n"
                "5. Provide rationale for each group\n"
                f"Minimum group size: {min_group_size}"
            ),
            expected_output=(
                "List of suggested groups, each with: group name, "
                "grouping dimension, device IDs, count, and rationale."
            ),
            agent=group_manager,
            human_input=True,
        )

        crew = Crew(
            agents=[group_manager],
            tasks=[group_task],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff()
        return {
            "agent": "Device Group Manager (LLM)",
            "type": "device_groups",
            "summary": str(result),
            "human_input_required": True,
        }


# ---------------------------------------------------------------------------
# Orchestrator — runs all Phase 1 agents
# ---------------------------------------------------------------------------

def run_all_agents(notify: bool = True,
                   firmware_version: Optional[str] = None,
                   min_group_size: int = 3) -> list[dict]:
    """Run all three Phase 1 agents and return their recommendations.

    Returns a list of agent result dicts suitable for dashboard display.
    """
    results = []

    try:
        results.append(run_ota_agent(firmware_version))
    except Exception as e:
        logger.exception("OTA agent failed")
        results.append({"agent": "OTA Campaign Strategist", "type": "ota_campaign",
                        "error": str(e)})

    try:
        results.append(run_anomaly_agent(notify=notify))
    except Exception as e:
        logger.exception("Anomaly agent failed")
        results.append({"agent": "Fleet Health Monitor", "type": "anomaly_check",
                        "error": str(e)})

    try:
        results.append(run_group_agent(min_group_size=min_group_size))
    except Exception as e:
        logger.exception("Group agent failed")
        results.append({"agent": "Device Group Manager", "type": "device_groups",
                        "error": str(e)})

    return results
