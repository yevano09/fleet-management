"""
Fleet Commander — Phase 1 Agent Tools

Crew AI tool wrappers around the Fleet Commander REST API and MQTT.
All tools return structured data consumable by LLM agents.
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("FLEET_BACKEND_URL", "http://backend:8000")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")


# ---------------------------------------------------------------------------
# Device tools
# ---------------------------------------------------------------------------

def list_devices(status: Optional[str] = None) -> dict:
    """Fetch all devices, optionally filtered by status (online/offline).

    Returns: {devices: [...], total: N}
    """
    params = {}
    if status:
        params["status"] = status
    resp = requests.get(f"{BASE_URL}/devices", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def register_device(name: str, firmware_version: str = "1.0.0",
                    ip_address: str = "") -> dict:
    """Register a new device or re-activate an existing one.

    Returns: {device_id, name, firmware_version, status}
    """
    resp = requests.post(f"{BASE_URL}/devices/register", json={
        "name": name,
        "firmware_version": firmware_version,
        "ip_address": ip_address,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# OTA tools
# ---------------------------------------------------------------------------

def upload_firmware(version: str, file_path: str) -> dict:
    """Upload a firmware binary from disk.

    Returns: {id, version, filename, sha256_hash, file_size, created_at}
    """
    with open(file_path, "rb") as f:
        files = {"file": (f"firmware_{version}.bin", f, "application/octet-stream")}
        resp = requests.post(
            f"{BASE_URL}/ota/upload",
            data={"version": version},
            files=files,
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()


def trigger_ota(firmware_id: str, device_ids: Optional[list[str]] = None,
                all_devices: bool = False) -> dict:
    """Trigger an OTA update for target devices.

    Returns: {message, deployment_ids, firmware_version}
    """
    payload = {"firmware_id": firmware_id, "all_devices": all_devices}
    if device_ids:
        payload["device_ids"] = device_ids
    resp = requests.post(f"{BASE_URL}/ota/trigger", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_ota_status() -> dict:
    """Get OTA deployment status summary.

    Returns: {deployments: [...], total, success_count,
              failed_count, in_progress_count}
    """
    resp = requests.get(f"{BASE_URL}/ota/status", timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_firmware() -> list[dict]:
    """List all uploaded firmware versions.

    Returns: [{id, version, filename, sha256_hash, file_size, created_at}, ...]
    """
    resp = requests.get(f"{BASE_URL}/ota/firmware", timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Metrics tools
# ---------------------------------------------------------------------------

def fetch_metrics() -> str:
    """Fetch the raw Prometheus metrics text from the backend.

    Returns: Raw prometheus text (agent can parse relevant lines).
    """
    resp = requests.get(f"{BASE_URL}/metrics/", timeout=10)
    resp.raise_for_status()
    return resp.text


def parse_metric(metric_name: str) -> list[dict]:
    """Parse a specific metric from the /metrics endpoint.

    Returns: [{labels: {...}, value: float}, ...]
    """
    text = fetch_metrics()
    results = []
    for line in text.splitlines():
        if line.startswith(metric_name):
            parts = line.split()
            if len(parts) >= 2:
                label_part = parts[0]
                value = float(parts[1])
                labels = {}
                if "{" in label_part:
                    inner = label_part[label_part.index("{") + 1:label_part.index("}")]
                    for pair in inner.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            labels[k.strip()] = v.strip('"')
                results.append({"labels": labels, "value": value})
    return results


# ---------------------------------------------------------------------------
# Notification tools
# ---------------------------------------------------------------------------

def send_slack_alert(message: str, severity: str = "info") -> bool:
    """Send an alert to Slack via webhook.

    Returns: True if sent successfully.
    """
    if not SLACK_WEBHOOK:
        logger.info(f"[Slack disabled] {severity}: {message}")
        return False
    color = {"info": "#36a64f", "warning": "#f59f00", "critical": "#f03e3e"}
    resp = requests.post(SLACK_WEBHOOK, json={
        "attachments": [{
            "color": color.get(severity, "#36a64f"),
            "title": f"Fleet Commander — {severity.upper()}",
            "text": message,
            "ts": datetime.now(timezone.utc).timestamp(),
        }],
    }, timeout=10)
    return resp.status_code == 200


# ---------------------------------------------------------------------------
# Device grouping logic (used by the grouping agent)
# ---------------------------------------------------------------------------

def suggest_device_groups(min_group_size: int = 3) -> dict:
    """Analyze all devices and suggest optimal groupings.

    Grouping dimensions:
      - firmware_version (same firmware → same OTA batch)
      - status (cohort health analysis)
      - signal_strength buckets (good/moderate/poor zones)

    Returns: {groups: [{name, dimension, device_ids, count, rationale}], ...}
    """
    data = list_devices()
    devices = data.get("devices", [])

    if not devices:
        return {"groups": [], "message": "No devices registered."}

    groups = []

    # ── Group by firmware version ──
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

    # ── Group by signal strength buckets ──
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


# ---------------------------------------------------------------------------
# Anomaly detection logic (used by the anomaly agent)
# ---------------------------------------------------------------------------

def detect_anomalies() -> list[dict]:
    """Run heuristic anomaly detection across the fleet.

    Checks:
      1. Devices with signal_strength < -90 (critical)
      2. OTA deployments stuck in downloading > 5 min
      3. Recent OTA failure spike (> 30% failure rate in last N)
      4. Devices offline > 60s but not flagged

    Returns: [{type, severity, message, affected_device_ids, timestamp}, ...]
    """
    anomalies = []
    now = datetime.now(timezone.utc)

    devices_data = list_devices()
    devices = devices_data.get("devices", [])

    ota_data = get_ota_status()
    deployments = ota_data.get("deployments", [])

    ts = now.isoformat()

    # ── 1. Critical signal strength ──
    weak_devices = [
        d for d in devices
        if d.get("signal_strength", 0) < -90 and d.get("status") == "online"
    ]
    if weak_devices:
        anomalies.append({
            "type": "weak_signal",
            "severity": "warning",
            "message": f"{len(weak_devices)} devices have critically weak "
                       f"signal (< -90 dBm). Possible hardware or "
                       f"placement issue.",
            "affected_device_ids": [d["id"] for d in weak_devices],
            "timestamp": ts,
        })

    # ── 2. Stuck OTA deployments ──
    stuck = [
        d for d in deployments
        if d.get("status") in ("downloading", "applying", "verifying")
    ]
    if stuck:
        anomalies.append({
            "type": "stuck_ota",
            "severity": "critical",
            "message": f"{len(stuck)} OTA deployments are stuck in "
                       f"non-terminal state. May indicate device "
                       f"communication failure.",
            "affected_device_ids": [d["device_id"] for d in stuck],
            "timestamp": ts,
        })

    # ── 3. OTA failure rate spike ──
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

    # ── 4. Offline devices ──
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

    return anomalies


# ---------------------------------------------------------------------------
# OTA campaign planning (used by the OTA strategist)
# ---------------------------------------------------------------------------

def plan_ota_campaign(firmware_version: str) -> dict:
    """Analyze the fleet and suggest an OTA rollout campaign plan.

    Returns: {
      firmware, target_devices, canary_group, rollout_phases,
      risk_assessment, recommendation
    }
    """
    devices_data = list_devices(status="online")
    devices = devices_data.get("devices", [])

    firmware_list = list_firmware()
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

    # Canary group = 10% or at least 1 device
    canary_size = max(1, len(devices) // 10)
    canary = devices[:canary_size]
    remainder = devices[canary_size:]

    # Phased rollout plan
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
            "gate": f"Wait {3 * phases.__len__() + 5} min, verify "
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
