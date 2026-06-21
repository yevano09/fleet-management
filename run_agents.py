#!/usr/bin/env python3
"""
Fleet Commander — Phase 1 Agent Runner

Usage:
  python run_agents.py                         # Run all agents
  python run_agents.py --ota                   # OTA campaign only
  python run_agents.py --anomaly               # Anomaly check only
  python run_agents.py --groups                # Device groups only
  python run_agents.py --v2g                   # V2G arbitrage dispatch
  python run_agents.py --v2g --horizon 12      # V2G with custom horizon
  python run_agents.py --onboard "Sensor-042"  # Onboard a new device (recommendation mode)
  python run_agents.py --onboard "Sensor-042" --onboard-firmware 2.0.0
  python run_agents.py --onboard "Sensor-042" --onboard-auto  # Register + push config
  python run_agents.py --firmware 2.0.0        # Target specific firmware
  python run_agents.py --no-notify             # Disable Slack notifications
  python run_agents.py --json                  # Output as JSON
"""

from __future__ import annotations

import json
import sys
import argparse
import os
import urllib.request

port = os.environ.get("FLEET_PORT", "8181")
# Ensure agents/tools.py reaches the host-accessible backend URL
os.environ.setdefault("FLEET_BACKEND_URL", f"http://localhost:{port}")

from agents.phase1_crew import (
    run_all_agents,
    run_ota_agent,
    run_anomaly_agent,
    run_group_agent,
    run_onboarding_agent,
    run_predictive_agent,
)

BACKEND_URL = os.environ.get("BACKEND_URL", f"http://localhost:{port}")


def _fetch_json(path: str) -> dict | list:
    url = f"{BACKEND_URL}{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def run_v2g_agent(horizon_hours: int = 24):
    try:
        result = _fetch_json(f"/agents/v2g-dispatch?horizon_hours={horizon_hours}")
        return {
            "agent": result.get("agent", "V2G Arbitrage Optimizer"),
            "type": "v2g_dispatch",
            "summary": result.get("summary", ""),
            "total_projected_revenue_dollars": result.get("total_projected_revenue_dollars", 0.0),
            "total_deg_cost_dollars": result.get("total_deg_cost_dollars", 0.0),
            "devices_used": result.get("devices_used", 0),
            "schedule": result.get("schedule", []),
        }
    except Exception as e:
        return {"agent": "V2G Arbitrage Optimizer", "type": "v2g_dispatch",
                "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Fleet Commander Phase 1 Agent Runner")
    parser.add_argument("--ota", action="store_true", help="Run OTA campaign agent only")
    parser.add_argument("--anomaly", action="store_true", help="Run anomaly detection agent only")
    parser.add_argument("--groups", action="store_true", help="Run device group agent only")
    parser.add_argument("--v2g", action="store_true", help="Run V2G arbitrage dispatch only")
    parser.add_argument("--onboard", type=str, metavar="NAME", default="",
                        help="Onboard a new device with the given name")
    parser.add_argument("--onboard-firmware", type=str, default="",
                        help="Firmware version for the onboarded device")
    parser.add_argument("--onboard-mqtt-id", type=str, default="",
                        help="MQTT client ID for the onboarded device")
    parser.add_argument("--onboard-ip", type=str, default="",
                        help="IP address for the onboarded device")
    parser.add_argument("--onboard-auto", action="store_true",
                        help="Auto-register the device (no human approval)")
    parser.add_argument("--firmware", type=str, default=None, help="Target firmware version")
    parser.add_argument("--min-group-size", type=int, default=3, help="Minimum group size")
    parser.add_argument("--horizon", type=int, default=24, help="V2G horizon in hours")
    parser.add_argument("--fleet-health", action="store_true", help="Run fleet health check + alert engine")
    parser.add_argument("--alerts", action="store_true", help="Show active alerts")
    parser.add_argument("--alerts-ack", type=str, metavar="ID", default="", help="Acknowledge alert by ID")
    parser.add_argument("--alerts-resolve", type=str, metavar="ID", default="", help="Resolve alert by ID")
    parser.add_argument("--remediate", action="store_true", help="Run Aegis remediation cycle")
    parser.add_argument("--remediation-history", action="store_true", help="Show remediation history")
    parser.add_argument("--remediation-rerun", type=str, metavar="ID", default="", help="Re-run a remediation by ID")
    parser.add_argument("--no-notify", action="store_true", help="Disable Slack notifications")
    parser.add_argument("--llm", action="store_true", help="Use CrewAI LLM agents (requires CREWAI_ENABLED=1)")
    parser.add_argument("--predictive", action="store_true", help="Run predictive maintenance scan")
    parser.add_argument("--predictive-history", action="store_true", help="Show failure predictions")
    parser.add_argument("--telemetry", type=str, metavar="DEVICE_ID", default="", help="Fetch telemetry for a device")
    parser.add_argument("--geofences", action="store_true", help="List geofences")
    parser.add_argument("--audit", action="store_true", help="Show recent audit log entries")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    notify = not args.no_notify

    if args.ota:
        results = [run_ota_agent(args.firmware)]
    elif args.anomaly:
        results = [run_anomaly_agent(notify=notify)]
    elif args.fleet_health:
        try:
            result = _fetch_json("/agents/fleet-health")
            results = [result]
        except Exception as e:
            results = [{"agent": "Fleet Health Monitor", "type": "fleet_health", "error": str(e)}]
    elif args.groups:
        results = [run_group_agent(args.min_group_size)]
    elif args.v2g:
        results = [run_v2g_agent(args.horizon)]
    elif args.onboard:
        results = [run_onboarding_agent(
            name=args.onboard,
            firmware_version=args.onboard_firmware,
            ip_address=args.onboard_ip,
            mqtt_client_id=args.onboard_mqtt_id,
            auto_register=args.onboard_auto,
        )]
    elif args.alerts:
        try:
            data = _fetch_json("/alerts/active")
            alerts = data.get("alerts", [])
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(f"\n{'='*60}")
                print(f"Active Alerts: {len(alerts)}")
                print(f"{'='*60}")
                for a in alerts:
                    icon = {"critical": "[CRIT]", "warning": "[WARN]", "info": "[INFO]"}.get(a.get("severity"), "[?]")
                    print(f"  {icon} {a.get('type', 'unknown')} (x{a.get('count', 1)})")
                    print(f"       {a.get('message', '')[:120]}")
                    print(f"       Status: {a.get('status', 'unknown')} | ID: {a.get('id', '')[:8]}")
            return
        except Exception as e:
            print(f"Error fetching alerts: {e}")
            return
    elif args.alerts_ack:
        try:
            resp = _fetch_json(f"/alerts/{args.alerts_ack}/acknowledge")
            print(json.dumps(resp, indent=2))
            return
        except Exception as e:
            print(f"Error acknowledging alert: {e}")
            return
    elif args.alerts_resolve:
        try:
            resp = _fetch_json(f"/alerts/{args.alerts_resolve}/resolve")
            print(json.dumps(resp, indent=2))
            return
        except Exception as e:
            print(f"Error resolving alert: {e}")
            return
    elif args.remediate:
        try:
            result = _fetch_json("/agents/aegis/scan")
            results = [result]
        except Exception as e:
            results = [{"agent": "Aegis Remediation Agent", "type": "remediation", "error": str(e)}]
    elif args.remediation_history:
        try:
            data = _fetch_json("/agents/aegis/history?limit=20")
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                remediations = data.get("remediations", [])
                print(f"\n{'='*60}")
                print(f"Remediation History: {data.get('total', 0)} total")
                print(f"{'='*60}")
                for r in remediations[:10]:
                    icon = {"success": "[OK]", "failed": "[FAIL]", "in_progress": "[...]", "escalated": "[!]", "dlq": "[DLQ]", "dry_run": "[DRY]"}.get(r.get("status", ""), "[?]")
                    print(f"  {icon} [{r.get('action_name', '?')}] {r.get('metric_name', '?')} = {r.get('value', '?')}")
                    print(f"       Rule: {r.get('rule_name', '?')} | Status: {r.get('status', '?')}")
                    print(f"       ID: {r.get('id', '')[:8]} | Duration: {r.get('duration_ms', '?')}ms")
            return
        except Exception as e:
            print(f"Error fetching remediation history: {e}")
            return
    elif args.remediation_rerun:
        try:
            resp = _fetch_json(f"/agents/aegis/rerun/{args.remediation_rerun}")
            print(json.dumps(resp, indent=2))
            return
        except Exception as e:
            print(f"Error re-running remediation: {e}")
            return
    elif args.predictive:
        results = [run_predictive_agent()]
    elif args.predictive_history:
        try:
            data = _fetch_json("/agents/predictive-history?min_risk=0.4&limit=20")
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                preds = data.get("predictions", [])
                print(f"\n{'='*60}")
                print(f"Failure Predictions: {data.get('total', 0)} total")
                print(f"{'='*60}")
                for p in preds[:10]:
                    risk = p.get("risk_score", 0)
                    icon = "[HIGH]" if risk >= 0.7 else "[MED]" if risk >= 0.4 else "[LOW]"
                    print(f"  {icon} {p.get('risk_type', '?')} risk={risk:.2f} device={p.get('device_id', '')[:8]}")
                    if p.get("recommendation"):
                        print(f"       -> {p['recommendation']}")
            return
        except Exception as e:
            print(f"Error fetching predictions: {e}")
            return
    elif args.telemetry:
        try:
            data = _fetch_json(f"/telemetry/{args.telemetry}?hours=24&limit=100")
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                points = data.get("points", [])
                print(f"\n{'='*60}")
                print(f"Telemetry for device {args.telemetry[:8]}... ({len(points)} points)")
                print(f"{'='*60}")
                for p in points[-10:]:
                    ts = p.get("timestamp", "")
                    sig = p.get("signal_strength", "?")
                    soc = p.get("soc", "?")
                    print(f"  {ts}  sig={sig} soc={soc}")
            return
        except Exception as e:
            print(f"Error fetching telemetry: {e}")
            return
    elif args.geofences:
        try:
            data = _fetch_json("/geofences")
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                gfs = data.get("geofences", [])
                print(f"\n{'='*60}")
                print(f"Geofences: {data.get('total', 0)} total")
                print(f"{'='*60}")
                for g in gfs:
                    status = "enabled" if g.get("enabled") else "disabled"
                    print(f"  [{status}] {g.get('name', '?')} ({g.get('shape', '?')}) id={g.get('id', '')[:8]}")
            return
        except Exception as e:
            print(f"Error fetching geofences: {e}")
            return
    elif args.audit:
        try:
            data = _fetch_json("/audit?limit=20")
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                logs = data.get("logs", [])
                print(f"\n{'='*60}")
                print(f"Audit Log: {data.get('total', 0)} total (showing 20)")
                print(f"{'='*60}")
                for l in logs:
                    print(f"  {l.get('timestamp', '')} {l.get('actor', '?')} -> {l.get('action', '?')} {l.get('target_type', '')}/{(l.get('target_id') or '')[:8]}")
            return
        except Exception as e:
            print(f"Error fetching audit log: {e}")
            return
    else:
        results = run_all_agents(
            notify=notify,
            firmware_version=args.firmware,
            min_group_size=args.min_group_size,
            use_llm=args.llm,
        )

    if args.json:
        print(json.dumps({"agents": results}, indent=2))
        return

    for r in results:
        print(f"\n{'='*60}")
        print(f"Agent: {r.get('agent', 'Unknown')}")
        print(f"Type: {r.get('type', 'N/A')}")
        print(f"{'='*60}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  Summary: {r.get('summary', 'N/A')}")
        if r.get("human_input_required"):
            print(f"  [!] Human approval required before execution")
        if r["type"] == "anomaly_check":
            anomalies = r.get("anomalies", [])
            print(f"  Anomalies: {len(anomalies)}")
            for a in anomalies[:5]:
                sev = a.get("severity", "info")
                icon = {"critical": "[CRIT]", "warning": "[WARN]", "info": "[INFO]"}.get(sev, "[?]")
                print(f"    {icon} [{sev}] {a.get('message', '')[:100]}")
        elif r["type"] == "device_groups":
            for g in r.get("groups", []):
                print(f"  - {g['name']}: {g['count']} devices ({g['dimension']})")
        elif r["type"] == "ota_campaign":
            details = r.get("details", {})
            phases = details.get("rollout_phases", [])
            print(f"  Online devices: {details.get('total_online_devices', 0)}")
            print(f"  Canary group: {details.get('canary_group', {}).get('device_count', 0)} devices")
            print(f"  Rollout phases: {len(phases)}")
            for p in phases:
                print(f"    - {p['phase']}: {p['device_count']} devices")
        elif r["type"] == "device_onboarding":
            details = r.get("details", {})
            conflicts = details.get("conflicts", [])
            device = details.get("device", {})
            fleet = details.get("fleet_state", {})
            print(f"  Onboarding possible: {details.get('onboarding_possible', False)}")
            print(f"  Registration: {details.get('registration_status', 'N/A')}")
            print(f"  Verification: {details.get('verification_status', 'N/A')}")
            print(f"  MQTT Config: {'pushed' if details.get('mqtt_config_pushed') else 'pending'}")
            recom = details.get("recommended_firmware", {})
            if recom:
                print(f"  Firmware: {recom.get('version', 'N/A')}")
            if conflicts:
                print(f"  Conflicts ({len(conflicts)}):")
                for c in conflicts:
                    print(f"    - [{c['type']}] {c['message']}")
            if device:
                print(f"  Device: {device.get('name', '')} ({device.get('id', '')[:8]}...)")
            if fleet:
                print(f"  Fleet: {fleet.get('total_devices', 0)} total, {fleet.get('online_devices', 0)} online")
        elif r["type"] == "v2g_dispatch":
            print(f"  Projected revenue: ${r.get('total_projected_revenue_dollars', 0.0):.2f}")
            print(f"  Degradation cost: ${r.get('total_deg_cost_dollars', 0.0):.2f}")
            schedule = r.get("schedule", [])
            active = [s for s in schedule if s.get("action") in ("charge", "discharge")]
            print(f"  Active slots: {len(active)} / {len(schedule)}")
            for s in active[:6]:
                icon = "[DISCHARGE]" if s["action"] == "discharge" else "[CHARGE]"
                print(f"    {icon} {s['action']} {s['power_kw']}kW at ${s['spot_price_per_kwh']:.4f}/kWh -> ${s['net_revenue_dollars']:+.2f}")


if __name__ == "__main__":
    main()
