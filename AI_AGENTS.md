# Fleet Commander — AI / Agentic Use Cases

## Overview

This document maps Crew AI agentic use cases onto the Fleet Commander platform. It covers:

1. **Phase 1 implementation** — Three running agent crews (OTA, Anomaly, Groups)
2. **Proposed agent crews** with detailed task breakdowns for future phases
3. **Future roadmap** for fully autonomous fleet operations

---

## 1. Current Platform Capabilities (Agent-Ready)

These existing Fleet Commander features provide the data surfaces and action primitives that AI agents can consume and control.

| Capability | Data / Action Surface | Consumable By Agent |
|---|---|---|
| Device registration & heartbeat | `GET /devices` — status, uptime, signal | Fleet Monitor Agent |
| Firmware upload & hash verification | `POST /ota/upload` — SHA256, version | OTA Manager Agent |
| OTA trigger (targeted/bulk) | `POST /ota/trigger` — deployment_ids | OTA Manager Agent |
| OTA status tracking | `GET /ota/status` — state machine transitions | OTA Manager Agent |
| Automatic rollback on hash mismatch | State machine: `hash_mismatch → rollback → rolled_back` | Self-Healing Agent |
| MQTT command publishing | `iot/fleet/{id}/command/ota` | Any agent with backend access |
| Prometheus metrics | `/metrics/` — active devices, OTA rates, latency | Monitoring Agent |
| Grafana dashboards | JSON dashboard model | Reporting Agent |
| **Agent Recommendations** | `GET /agents/*` — OTA plans, anomaly checks, device groups | Human-in-loop interface |

### Key Data Points for Agents

```
Device: {id, name, firmware_version, status, signal_strength,
         last_seen, uptime_percentage, previous_firmware_version}

Deployment: {id, firmware_id, device_id, status, retry_count,
             error_message, created_at, updated_at}

Metrics: fleet_active_devices, fleet_ota_deployments_total{status},
         fleet_api_request_latency_seconds, fleet_mqtt_messages_*
```

---

## 2. Phase 1 — Assisted Mode (Implemented)

Three AI agent crews are implemented and running. All run in **tool-only mode** (no LLM dependency) by default, with optional Crew AI LLM integration.

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Fleet Commander Backend                     │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐              │
│  │  Agent     │  │  Agent     │  │  Device    │              │
│  │  Router    │  │  Tools     │  │  Group     │              │
│  │  /agents/* │  │  (DB)      │  │  Logic     │              │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘              │
│        │               │               │                     │
│  ┌─────┴───────────────┴───────────────┴──────────────────┐  │
│  │              SQLAlchemy (Direct DB Access)               │  │
│  └──────────────────────────┬──────────────────────────────┘  │
│                             │                                 │
│  ┌──────────────────────────┴──────────────────────────────┐  │
│  │              SQLite / PostgreSQL                         │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
         │
         │ HTTP (standalone CLI mode)
         ▼
┌──────────────────────────────────────────────────────────────┐
│              Fleet Commander REST API                         │
│  GET /devices, GET /ota/status, GET /ota/firmware             │
└──────────────────────────────────────────────────────────────┘
```

**Dual execution mode:**
- **In-backend**: Agent tools query the database directly via SQLAlchemy (`agents/async_tools.py`) to avoid self-referencing HTTP deadlocks
- **Standalone CLI**: Same tools use HTTP (`agents/tools.py`) when run outside the container

### Agent 1: OTA Campaign Strategist

Suggests a phased rollout plan with canary group, rollout phases, and gate criteria.

**Endpoint:** `GET /agents/ota-campaign?firmware_version=2.0.0`

**Response (example):**
```json
{
  "agent": "OTA Campaign Strategist",
  "type": "ota_campaign",
  "summary": "Deploy firmware 2.0.0 to 1 canary devices first. Monitor for 120s.",
  "details": {
    "firmware": {"id": "...", "version": "2.0.0"},
    "total_online_devices": 5,
    "canary_group": {
      "device_count": 1,
      "monitor_duration_seconds": 120,
      "pass_criteria": "failure_rate < 20% AND no critical anomalies"
    },
    "rollout_phases": [
      {"phase": "Phase 1", "device_count": 1, "gate": "..."},
      {"phase": "Phase 2", "device_count": 2, "gate": "..."},
      {"phase": "Phase 3", "device_count": 2, "gate": "No gate (final phase)"}
    ],
    "risk_assessment": {"level": "low", "note": "..."}
  },
  "human_input_required": true
}
```

### Agent 2: Fleet Health Monitor

Scans the fleet for anomalies: weak signals, stuck OTAs, failure spikes, mass offline events.

**Endpoint:** `GET /agents/anomaly-check?notify=false`

**Heuristic checks:**
1. Devices with signal_strength < -90 dBm → warning
2. OTA deployments stuck in downloading/applying/verifying → critical
3. OTA failure rate > 30% (when total >= 5) → critical
4. More than 30% of devices offline → critical

**Notifications:** Sends Slack alerts via `SLACK_WEBHOOK_URL` env var for critical/warning anomalies.

### Agent 3: Device Group Manager

Suggests device groupings by firmware version and signal strength with rationale.

**Endpoint:** `GET /agents/device-groups?min_group_size=3`

**Grouping dimensions:**
- **Firmware version** — same-firmware cohorts for OTA targeting
- **Signal strength buckets** — good (>= -60), moderate (-60 to -80), poor (< -80) dBm

### Combined Endpoint

**`GET /agents/recommendations?notify=false`** — runs all three agents in parallel and returns combined results. Consumed by the dashboard.

### Optional Crew AI LLM Mode

Set `CREWAI_ENABLED=1` and install `crewai` to use LLM-powered agents:

```bash
pip install crewai
CREWAI_ENABLED=1 python run_agents.py --ota  # Uses LLM for reasoning
```

Crew AI agents are defined in `agents/phase1_crew.py` with proper `Agent`, `Task`, and `Crew` objects, `human_input=True` gates, and tool bindings.

### File Structure

```
fleet-management/
├── agents/
│   ├── __init__.py          # Package init
│   ├── tools.py             # HTTP-based tools (standalone CLI usage)
│   ├── async_tools.py       # Async DB-backed tools (in-backend usage)
│   ├── phase1_crew.py       # Crew AI agent definitions + tool-only fallbacks
│   └── routers.py           # FastAPI router exposing /agents/* endpoints
└── run_agents.py            # CLI runner (--ota, --anomaly, --groups, --json)
```

### Quick Start

```bash
# Dashboard shows agent recommendations (auto-refresh every 30s)
docker compose --profile demo up -d
open http://localhost:8000

# CLI: full recommendation report
python run_agents.py

# CLI: specific agent
python run_agents.py --ota

# CLI: JSON output for programmatic use
python run_agents.py --json
```

---

## 3. Proposed Crew AI Agent Crews (Future Phases)

### Crew A — OTA Campaign Manager

**Goal:** Autonomous OTA rollout with canary analysis, gradual rollout, and automatic rollback decisions.

```
Crew: OTA Campaign Crew
├── Agent: OTA Strategist (lead)
│   Role: Plans the rollout campaign
│   Tools: GET /ota/firmware, GET /devices
│   Goal: Select target firmware, compute canary group (10%),
│          define rollback criteria (≥2 failures → abort)
│
├── Agent: Canary Analyzer
│   Role: Monitors canary group health
│   Tools: GET /devices, GET /ota/status, /metrics/
│   Goal: After canary deploy, analyze for N minutes.
│          Signal: if failure_rate > threshold → abort + rollback
│          Signal: if all success → proceed to full rollout
│
├── Agent: Rollout Executor
│   Role: Executes phased rollout
│   Tools: POST /ota/trigger
│   Goal: Deploy to 30% → wait → 60% → wait → 100%.
│          Each phase calls Canary Analyzer for gate check.
│
└── Agent: Rollback Commander
    Role: Executes rollback on failure
    Tools: POST /ota/trigger (with previous firmware_id)
    Goal: Revert all devices to previous known-good firmware.
           Log incident with full timeline.
```

### Crew B — Fleet Health & Anomaly Detection

**Goal:** Real-time monitoring across 3 horizons (real-time, trend, predictive).

```
Crew: Fleet Health Crew
├── Agent: Real-Time Monitor
│   Role: Watches live fleet metrics
│   Triggers: Every 30s via /metrics/
│   Signals: Device goes offline, OTA failure spike,
│            signal strength drops across cohort
│   Action: Alert → escalate to Diagnostician Agent
│
├── Agent: Trend Analyst
│   Role: Analyzes 1h/24h/7d trends
│   Tools: Prometheus range queries, /devices diff
│   Signals: Gradual uptime decline, regional signal degradation,
│            OTA success rate trending down
│   Action: Generate report, flag for human review
│
├── Agent: Predictive Diagnostician
│   Role: Correlates signals to diagnose root cause
│   Tools: Historical OTA data, device metadata, MQTT logs
│   Signals: "Devices on firmware 1.2.0 have 3x higher
│            offline rate than those on 1.1.0"
│   Action: Recommend OTA rollback or targeted fix
│
└── Agent: Incident Responder
    Role: Executes automated mitigations
    Tools: POST /devices/{id}/heartbeat (force re-register),
           POST /ota/trigger (rollback),
           MQTT command/config push
    Actions: Isolate malfunctioning device cohort,
             Trigger rollback, notify admin
```

**Example Anomaly Scenario:**

```
1. Real-Time Monitor detects signal_strength of Device-042 drops
   from -55dBm to -90dBm over 5 minutes
2. Trend Analyst confirms: same pattern on 6 other devices
   on same firmware version
3. Predictive Diagnostician correlates: all 7 devices received
   OTA to firmware 2.0.0 in last hour
4. Incident Responder: triggers rollback of 2.0.0 for the cohort,
   publishes remote config to reduce TX power
5. OTA Campaign Manager (Crew A) notified: add firmware 2.0.0
   to blocklist, flag for engineering review
```

---

### Crew C — Device Lifecycle Management

**Goal:** Autonomous device onboarding, provisioning, and retirement.

```
Crew: Device Lifecycle Crew
├── Agent: Onboarding Manager
│   Role: Processes new device registrations
│   Triggers: MQTT register topic, /devices/register API
│   Tasks:
│     1. Validate device identity (check against allowlist)
│     2. Assign device group (based on model, location)
│     3. Push initial config via MQTT command/config
│     4. Add to monitoring dashboard
│     5. Record in device registry
│
├── Agent: Config Compliance
│   Role: Ensures all devices run approved config
│   Periodic: Scans /devices every 6h
│   Tasks:
│     1. Check firmware_version against approved list
│     2. Check last_seen within threshold
│     3. Flag out-of-compliance devices
│     4. Trigger OTA or config push to remediate
│
└── Agent: Decommissioning Manager
    Role: Handles device retirement
    Triggers: Manual request or inactivity > 30 days
    Tasks:
    1. Verify device identity
    2. Push factory-reset command via MQTT
    3. Archive device records to cold storage
    4. Remove from active monitoring
    5. Log decommission certificate
```

---

### Crew D — Security Incident Response

**Goal:** Detect and respond to security threats across the fleet.

```
Crew: Security Crew
├── Agent: Threat Detector
│   Role: Identifies anomalous device behavior
│   Signals: - Rapid registration/de-registration cycling
│            - Heartbeats from unexpected geographies
│            - OTA hash mismatch rate exceeds threshold
│            - Unexpected MQTT topic access patterns
│   Tools: /metrics, MQTT subscribe (anomaly topics)
│
├── Agent: Investigative Analyst
│   Role: Deep-dives into flagged anomalies
│   Tools: /devices history, /ota/status timeline,
│          MQTT log correlation
│   Output: Incident report with severity, affected devices,
│           timeline, recommended actions
│
└── Agent: Mitigation Specialist
    Role: Executes containment and recovery
    Tools: MQTT command/config (disable device),
           /ota/trigger (force rollback),
           config push (quarantine mode)
    Actions: - Quarantine compromised devices
             - Block affected firmware version
             - Trigger fleet-wide security advisory
             - Notify incident response team
```

---

## 4. Agent Integration Architecture

### How Crew AI Connects to Fleet Commander

```
┌─────────────────────────────────────────────────────────┐
│                   Crew AI Orchestrator                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ OTA      │  │ Fleet    │  │ Device   │  │ Security │ │
│  │ Campaign │  │ Health   │  │ Lifecycle│  │ Response │ │
│  │ Crew     │  │ Crew     │  │ Crew     │  │ Crew     │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘ │
│       │              │             │              │        │
└───────┼──────────────┼─────────────┼──────────────┼────────┘
        │              │             │              │
        ▼              ▼             ▼              ▼
┌──────────────────────────────────────────────────────────┐
│              Agent Tool Layer (Crew AI Tools)              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ API Tools│  │ MQTT     │  │ Metrics  │  │ Database │  │
│  │ (HTTP)   │  │ Client   │  │ (PromQL) │  │ (SQL)    │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
└───────┼──────────────┼─────────────┼──────────────┼────────┘
        │              │             │              │
        ▼              ▼             ▼              ▼
┌──────────────────────────────────────────────────────────┐
│              Fleet Commander Platform                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ FastAPI  │  │ Mosquitto│  │Prometheus│  │ SQLite/  │  │
│  │ Backend  │  │ MQTT     │  │+ Grafana │  │ Postgres │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Phase 1 Specific Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                     Fleet Commander Backend                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ Phase 1 Crew │  │ Phase 1 Crew │  │ Phase 1 Crew │            │
│  │ OTA          │  │ Anomaly      │  │ Device Group │            │
│  │ Strategist   │  │ Detector     │  │ Manager      │            │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘            │
│         │                 │                 │                     │
│  ┌──────┴─────────────────┴─────────────────┴──────────────────┐ │
│  │              agents/async_tools.py                           │ │
│  │  (Direct DB queries via SQLAlchemy — no HTTP loop)          │ │
│  └──────────────────────────────┬───────────────────────────────┘ │
│                                 │                                 │
│  ┌──────────────────────────────┴───────────────────────────────┐ │
│  │                    SQLite / PostgreSQL                        │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                 │                                 │
│  ┌──────────────────────────────┴───────────────────────────────┐ │
│  │              agents/routers.py (FastAPI)                      │ │
│  │  GET /agents/recommendations, /ota-campaign,                 │ │
│  │  /anomaly-check, /device-groups                              │ │
│  └──────────────────────────────┬───────────────────────────────┘ │
└─────────────────────────────────┼─────────────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │  Fleet Dashboard (HTMX)    │
                    │  Agent panels auto-refresh  │
                    │  every 30 seconds           │
                    └───────────────────────────┘

Standalone mode (outside Docker):
  CLI ─── agents/tools.py (HTTP) ─── Fleet Commander API
```

### Tool Definitions

Each Crew AI agent needs tools that wrap Fleet Commander's API:

```python
from crewai.tools import tool
import requests

BASE = "http://localhost:8000"

@tool("List Devices")
def list_devices(status: str = None) -> list:
    """Fetch all devices, optionally filtered by status (online/offline)"""
    params = {"status": status} if status else {}
    resp = requests.get(f"{BASE}/devices", params=params)
    return resp.json()["devices"]

@tool("Trigger OTA")
def trigger_ota(firmware_id: str, device_ids: list[str] = None,
                all_devices: bool = False) -> dict:
    """Trigger an OTA firmware update for target devices"""
    payload = {"firmware_id": firmware_id, "all_devices": all_devices}
    if device_ids:
        payload["device_ids"] = device_ids
    resp = requests.post(f"{BASE}/ota/trigger", json=payload)
    return resp.json()

@tool("Get OTA Status")
def get_ota_status() -> dict:
    """Get status of all OTA deployments with success/fail counts"""
    resp = requests.get(f"{BASE}/ota/status")
    return resp.json()

@tool("Query Metrics")
def query_metrics(query: str) -> list:
    """Run a PromQL query against the /metrics endpoint"""
    resp = requests.get(f"{BASE}/metrics/")
    text = resp.text
    results = []
    for line in text.splitlines():
        if line.startswith(query) or query in line:
            results.append(line)
    return results

@tool("Push Remote Config")
def push_config(device_id: str, config: dict) -> bool:
    """Push a remote configuration to a device via MQTT"""
    resp = requests.post(f"{BASE}/devices/{device_id}/config", json=config)
    return resp.status_code == 200
```

---

## 5. Future Roadmap with AI/Agents

### Phase 1 — Assisted (Implemented)

| Feature | Description | Value | Status |
|---------|-------------|-------|--------|
| OTA Campaign Dashboard | Dashboard UI shows agent-recommended rollout plan | Human approves, agent executes | ✅ Live |
| Anomaly Alerts | Agent detects anomalies, sends Slack/email with diagnosis | Faster incident response | ✅ Live |
| Device Group Manager | Agent suggests device groupings based on firmware/signal/location | Better targeting | ✅ Live |

### Phase 2 — Semi-Autonomous (3–6 months)

| Feature | Description | Value |
|---------|-------------|-------|
| Canary Auto-Promote | Agent runs canary, auto-promotes to full rollout if healthy | Zero-touch OTA for low-risk updates |
| Self-Healing Fleet | Agent detects offline device cohort, auto-triggers recovery | Reduced MTTR |
| Predictive Rollback | Agent predicts OTA failure risk based on historical patterns | Prevent incidents before they happen |
| Fleet Capacity Planner | Agent analyzes growth, recommends scaling backend/MQTT | Cost-optimized infrastructure |

### Phase 3 — Autonomous (6–12 months)

| Feature | Description | Value |
|---------|-------------|-------|
| Fully Autonomous OTA | Agent manages end-to-end: plan → canary → rollout → verify → rollback if needed | Hands-off fleet updates |
| Cross-Fleet Optimization | Agent learns optimal OTA parameters (batch size, delay, time-of-day) per device cohort | Maximized success rate |
| Security Auto-Response | Agent detects zero-day, quarantines affected devices, prepares emergency patch | Containment in minutes |
| Natural Language Fleet Ops | "Roll back firmware 2.0.0 from all devices in Europe" → agent plans and executes | LLM-driven fleet management |

### Phase 4 — Predictive & Generative (12+ months)

| Feature | Description | Value |
|---------|-------------|-------|
| Generative Firmware Patches | Agent generates hotfix firmware for critical CVEs | Patch in hours, not weeks |
| Predictive Device Health | Agent predicts device failure 7 days in advance using ML on heartbeat data | Proactive maintenance |
| AI Fleet Twin | Digital twin of the fleet running simulations before real rollout | Zero-risk OTA planning |
| Multi-Fleet Orchestration | Agent manages across multiple independent fleets with shared policies | Unified management at scale |

---

## 6. Human-in-Loop Design

All autonomous actions include human-in-loop gates:

```
Level 1 — Notify Only
  Agent: "Device-042 signal dropped 40dBm"
  → Logged, dashboard badge, optional Slack

Level 2 — Recommend
  Agent: "Recommended: roll back firmware 2.0.0 from 7 affected devices"
  → Dashboard notification with Accept/Reject buttons

Level 3 — Auto-Execute with Undo
  Agent: "Rolling back firmware 2.0.0 from 7 devices. Undo available for 5 minutes."
  → Auto-executes, undo button on dashboard

Level 4 — Full Autonomous
  Agent: "OTA campaign completed: 142/150 success, 8 rolled back. Report generated."
  → Post-execution summary only
```

Each Crew AI task specifies its human-in-loop level:

```python
canary_analysis = Task(
    description="Analyze canary group health for OTA deployment",
    expected_output="Go/No-Go recommendation with evidence",
    agent=canary_analyzer,
    human_input=True,  # Requires human approval before next phase
)
```

Phase 1 agents operate at **Level 2 (Recommend)** — all outputs include `"human_input_required": true`.

---

## 7. Quick Start: Running the Agents

### Dashboard (Agent Panels)

```bash
docker compose --profile demo up -d
open http://localhost:8000
```
The "Agent Recommendations" section at the bottom of the dashboard auto-refreshes every 30 seconds.

### CLI Runner

```bash
# Run all three agents
python run_agents.py

# OTA campaign only
python run_agents.py --ota --firmware 2.0.0

# Anomaly check only (with Slack alerts)
python run_agents.py --anomaly

# Device groups only
python run_agents.py --groups --min-group-size 3

# JSON output
python run_agents.py --json
```

### REST API

```bash
# All recommendations
curl http://localhost:8000/agents/recommendations?notify=false

# OTA campaign plan
curl 'http://localhost:8000/agents/ota-campaign?firmware_version=2.0.0'

# Anomaly check
curl 'http://localhost:8000/agents/anomaly-check?notify=false'

# Device groupings
curl 'http://localhost:8000/agents/device-groups?min_group_size=3'
```

### Crew AI LLM Mode (Optional)

```bash
pip install crewai
CREWAI_ENABLED=1 python run_agents.py --ota  # Uses LLM reasoning
```

When `CREWAI_ENABLED=1`, the `run_ota_agent_llm()`, `run_anomaly_agent_llm()`, and `run_group_agent_llm()` functions are used instead of the heuristic fallbacks. Each uses proper `Crew` objects with `Agent`, `Task`, and `Process.sequential`.

---

## 8. Appendix — Agent Tool Catalog

| Tool Name | Endpoint / Action | Agent Crew |
|-----------|------------------|------------|
| `list_devices` | `GET /devices` | OTA, Health, Lifecycle |
| `register_device` | `POST /devices/register` | Lifecycle |
| `send_heartbeat` | `POST /devices/{id}/heartbeat` | Health (test) |
| `upload_firmware` | `POST /ota/upload` | OTA |
| `trigger_ota` | `POST /ota/trigger` | OTA, Security |
| `get_ota_status` | `GET /ota/status` | OTA, Health |
| `list_firmware` | `GET /ota/firmware` | OTA |
| `query_metrics` | `GET /metrics/` | All crews |
| `query_prometheus` | Prometheus HTTP API (`:9090/api/v1/query`) | Health |
| `push_mqtt_config` | MQTT `iot/fleet/{id}/command/config` | Lifecycle, Security |
| `send_slack_alert` | External Slack webhook | All crews |
| `plan_ota_campaign` | Heuristic: firmware+devices → canary+phases | OTA Strategist |
| `detect_anomalies` | Heuristic: devices+ota → anomaly list | Fleet Health |
| `suggest_device_groups` | Heuristic: devices → grouped cohorts | Device Group Manager |

### Phase 1 Agent Routes

| Route | Agent | Human Input Required |
|-------|-------|---------------------|
| `GET /agents/recommendations` | All three agents | Yes (OTA + Groups) |
| `GET /agents/ota-campaign` | OTA Campaign Strategist | Yes |
| `GET /agents/anomaly-check` | Fleet Health Monitor | No (Level 1 — Notify) |
| `GET /agents/device-groups` | Device Group Manager | Yes |
