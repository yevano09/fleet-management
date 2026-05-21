#!/usr/bin/env python3
"""
Fleet Commander — Phase 1 Agent Runner

Usage:
  python run_agents.py                         # Run all agents
  python run_agents.py --ota                   # OTA campaign only
  python run_agents.py --anomaly               # Anomaly check only
  python run_agents.py --groups                # Device groups only
  python run_agents.py --firmware 2.0.0        # Target specific firmware
  python run_agents.py --no-notify             # Disable Slack alerts
  python run_agents.py --json                  # Output as JSON
"""

from __future__ import annotations

import json
import sys
import argparse

from agents.phase1_crew import (
    run_all_agents,
    run_ota_agent,
    run_anomaly_agent,
    run_group_agent,
)


def main():
    parser = argparse.ArgumentParser(description="Fleet Commander Phase 1 Agent Runner")
    parser.add_argument("--ota", action="store_true", help="Run OTA campaign agent only")
    parser.add_argument("--anomaly", action="store_true", help="Run anomaly detection agent only")
    parser.add_argument("--groups", action="store_true", help="Run device group agent only")
    parser.add_argument("--firmware", type=str, default=None, help="Target firmware version")
    parser.add_argument("--min-group-size", type=int, default=3, help="Minimum group size")
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
            print(f"  ⚠  Human approval required before execution")
        if r["type"] == "anomaly_check":
            anomalies = r.get("anomalies", [])
            print(f"  Anomalies: {len(anomalies)}")
            for a in anomalies[:5]:
                sev = a.get("severity", "info")
                icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "⚪")
                print(f"    {icon} [{sev}] {a.get('message', '')[:100]}")
        elif r["type"] == "device_groups":
            for g in r.get("groups", []):
                print(f"  • {g['name']} — {g['count']} devices ({g['dimension']})")
        elif r["type"] == "ota_campaign":
            details = r.get("details", {})
            phases = details.get("rollout_phases", [])
            print(f"  Online devices: {details.get('total_online_devices', 0)}")
            print(f"  Canary group: {details.get('canary_group', {}).get('device_count', 0)} devices")
            print(f"  Rollout phases: {len(phases)}")
            for p in phases:
                print(f"    - {p['phase']}: {p['device_count']} devices")


if __name__ == "__main__":
    main()
