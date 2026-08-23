"""
Fleet Commander — Phase 1 Crew AI Agents

Three AI agent crews for the Assisted Phase:
  1. OTA Campaign Agent — Recommends canary-based rollout plans
  2. Anomaly Detection Agent — Detects fleet anomalies and alerts
  3. Device Group Manager — Suggests device groupings
  4. Device Onboarding Agent — Guides new device introduction to the fleet

Each crew can run in two modes:
  - LLM mode (with Crew AI + API key) for natural-language reasoning
  - Tool-only mode (heuristic logic) for standalone operation

Usage:
  from agents.phase1_crew import run_all_agents, run_ota_agent, run_anomaly_agent, run_group_agent, run_onboarding_agent
"""

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

    If notify=True, routes through the backend's /agents/fleet-health endpoint
    which uses the AlertEngine for dedup, persistence, and multi-channel
    notification (Bug 7 fix — previously sent raw Slack messages only).
    Returns all anomalies found.
    """
    # When notifying, use the backend endpoint which runs the full alert pipeline
    if notify:
        try:
            import urllib.request, json
            base = os.environ.get("FLEET_BACKEND_URL", "http://localhost:8181")
            api_key = os.environ.get("FLEET_API_KEY", "")
            req = urllib.request.Request(f"{base}/agents/fleet-health")
            if api_key:
                req.add_header("X-API-Key", api_key)  # P0 UC-23: strict-mode auth
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("Fleet-health endpoint failed, falling back to local analysis: %s", e)

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

    return {
        "agent": "Fleet Health Monitor",
        "type": "anomaly_check",
        "status": "anomalies_found",
        "summary": (
            f"Found {len(critical)} critical and {len(warnings)} "
            f"warning anomalies."
        ),
        "anomalies": anomalies,
        "notifications_sent": False,
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
# 4. Device Onboarding Agent
# ---------------------------------------------------------------------------

def run_onboarding_agent(
    name: str = "",
    firmware_version: str = "",
    ip_address: str = "",
    mqtt_client_id: str = "",
    auto_register: bool = False,
) -> dict:
    """Recommend and optionally execute onboarding of a new device into the fleet.

    When auto_register=False (default), returns the onboarding plan for
    human review with human_input_required=True.
    When auto_register=True, registers the device, pushes initial config,
    and checks heartbeat verification.
    """
    if not name:
        return {"error": "Device name is required for onboarding."}

    plan = tools.onboard_device(
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
        mqtt_result = tools.push_remote_config(
            device_id=device["id"],
            config=plan["initial_config"],
        )
        mqtt_config_pushed = mqtt_result

        devices_after = tools.list_devices()
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


# ---------------------------------------------------------------------------
# Orchestrator — runs all Phase 1 agents
# ---------------------------------------------------------------------------

def run_all_agents(notify: bool = True,
                   firmware_version: Optional[str] = None,
                   min_group_size: int = 3,
                   use_llm: bool = False) -> list[dict]:
    """Run all Phase 1 agents and return their recommendations.

    Returns a list of agent result dicts suitable for dashboard display.
    When use_llm=True and CrewAI is enabled, uses the LLM variants (Bug 8 fix).
    """
    results = []

    if use_llm and USE_CREWAI:
        try:
            results.append(run_ota_agent_llm(firmware_version))
        except Exception as e:
            logger.exception("OTA LLM agent failed")
            results.append({"agent": "OTA Campaign Strategist (LLM)", "type": "ota_campaign", "error": str(e)})

        try:
            results.append(run_anomaly_agent_llm(notify=notify))
        except Exception as e:
            logger.exception("Anomaly LLM agent failed")
            results.append({"agent": "Fleet Health Monitor (LLM)", "type": "anomaly_check", "error": str(e)})

        try:
            results.append(run_group_agent_llm(min_group_size=min_group_size))
        except Exception as e:
            logger.exception("Group LLM agent failed")
            results.append({"agent": "Device Group Manager (LLM)", "type": "device_groups", "error": str(e)})

        return results

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


# ---------------------------------------------------------------------------
# 5. Aegis Remediation Agent (Sprint 2)
# ---------------------------------------------------------------------------

def run_remediation_agent() -> dict:
    """Run the Aegis remediation cycle: check resource pressure, evaluate, remediate.

    Returns agent dict with signal summary and remediation results.
    """
    try:
        result = tools.run_remediation_cycle()
        history = tools.get_remediation_history(limit=5)

        if result.get("cycle_completed"):
            return {
                "agent": "Aegis Remediation Agent",
                "type": "remediation",
                "summary": "Remediation cycle completed. Check history for details.",
                "details": {
                    "cycle_completed": True,
                    "recent_remediations": history.get("remediations", []),
                    "total_history": history.get("total", 0),
                },
            }
        else:
            return {
                "agent": "Aegis Remediation Agent",
                "type": "remediation",
                "summary": f"Remediation cycle failed: {result.get('error', 'unknown')}",
                "details": {"cycle_completed": False, "error": result.get("error")},
            }
    except Exception as e:
        logger.exception("Remediation agent failed")
        return {"agent": "Aegis Remediation Agent", "type": "remediation",
                "error": str(e)}


# ---------------------------------------------------------------------------
# 6. Predictive Maintenance Agent (Feature 3)
# ---------------------------------------------------------------------------

def run_predictive_agent() -> dict:
    """Run the Predictive Maintenance Agent: analyze telemetry trends and predict failures.

    Returns agent dict with prediction summary and details.
    """
    try:
        result = tools.run_predictive_scan()
        if "error" in result:
            return {
                "agent": "Predictive Maintenance Agent",
                "type": "predictive_maintenance",
                "error": result["error"],
            }
        details = result.get("details", {})
        predictions = details.get("predictions", [])
        high_risk = [p for p in predictions if p.get("risk_score", 0) >= 0.7]
        medium_risk = [p for p in predictions if 0.4 <= p.get("risk_score", 0) < 0.7]

        return {
            "agent": "Predictive Maintenance Agent",
            "type": "predictive_maintenance",
            "summary": (
                f"Found {len(high_risk)} high-risk and {len(medium_risk)} "
                f"medium-risk failure predictions."
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
    except Exception as e:
        logger.exception("Predictive agent failed")
        return {"agent": "Predictive Maintenance Agent", "type": "predictive_maintenance",
                "error": str(e)}
