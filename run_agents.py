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

# Ensure agents/tools.py reaches the host-accessible backend URL
os.environ.setdefault("FLEET_BACKEND_URL", "http://localhost:8000")

from agents.phase1_crew import (
    run_all_agents,
    run_ota_agent,
    run_anomaly_agent,
    run_group_agent,
)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


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
    parser.add_argument("--firmware", type=str, default=None, help="Target firmware version")
    parser.add_argument("--min-group-size", type=int, default=3, help="Minimum group size")
    parser.add_argument("--horizon", type=int, default=24, help="V2G horizon in hours")
    parser.add_argument("--no-notify", action="store_true", help="Disable Slack notifications")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    notify = not args.no_notify

    if args.ota:
        results = [run_ota_agent(args.firmware)]
    elif args.anomaly:
        results = [run_anomaly_agent(notify=notify)]
    elif args.groups:
        results = [run_group_agent(args.min_group_size)]
    elif args.v2g:
        results = [run_v2g_agent(args.horizon)]
    else:
        results = run_all_agents(
            notify=notify,
            firmware_version=args.firmware,
            min_group_size=args.min_group_size,
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
