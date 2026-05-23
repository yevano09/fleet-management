# Fleet Commander — Customer User Documentation (CUDO)

> **Version:** 1.0.0  
> **Document Date:** 2026-05-23  
> **System:** Fleet Commander IoT Device Management Module

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [System Architecture](#2-system-architecture)
3. [Installation & Deployment](#3-installation--deployment)
4. [Web Dashboard Usage](#4-web-dashboard-usage)
5. [Device Management](#5-device-management)
6. [OTA Firmware Updates](#6-ota-firmware-updates)
7. [V2G (Vehicle-to-Grid) Arbitrage](#7-v2g-vehicle-to-grid-arbitrage)
8. [AI Agent Recommendations](#8-ai-agent-recommendations)
9. [MQTT Protocol Reference](#9-mqtt-protocol-reference)
10. [REST API Reference](#10-rest-api-reference)
11. [Prometheus Metrics](#11-prometheus-metrics)
12. [Grafana Observability](#12-grafana-observability)
13. [Configuration Reference](#13-configuration-reference)
14. [Boundary Conditions & Limitations](#14-boundary-conditions--limitations)
15. [Troubleshooting](#15-troubleshooting)
16. [Security Considerations](#16-security-considerations)
17. [Production Deployment](#17-production-deployment)
18. [ESP32 / IoT Device Connection](#18-esp32--iot-device-connection)

---

## 1. System Overview

Fleet Commander is a production-grade **IoT device fleet management system**. It enables users to:

- **Register and manage** thousands of IoT devices (EV chargers, sensors, gateways, ESP32s)
- **Monitor device health** in real-time via a web dashboard
- **Push OTA firmware updates** to individual devices or the entire fleet
- **Schedule V2G (Vehicle-to-Grid) charge/discharge** for electric vehicle batteries
- **Visualise fleet metrics** with Prometheus + Grafana dashboards
- **Receive AI-powered recommendations** for OTA rollouts, anomaly detection, and device grouping
- **Control devices remotely** via MQTT messaging

### 1.1 Key Capabilities

| Capability | Description |
|---|---|
| Device auto-registration | Devices register on first MQTT connect; no manual setup |
| Heartbeat monitoring | Periodic heartbeats track uptime, signal strength, battery SOC |
| OTA firmware updates | Upload firmware, trigger updates, automatic rollback on failure |
| V2G arbitrage | Schedule EV battery charge/discharge to maximise revenue |
| AI agents | OTA campaign planning, anomaly detection, device grouping |
| Prometheus metrics | 14+ built-in metrics for fleet health, OTA, MQTT, API latency |
| Grafana dashboards | Pre-built visualisation with auto-provisioning |
| Remote device configuration | Push configs to devices via MQTT |
| ESP32 support | Full Arduino sketch available for real hardware |

---

## 2. System Architecture

```
┌──────────────┐    MQTT v5      ┌──────────────┐   HTTP/REST   ┌────────────────┐
│  IoT Devices │◄──────────────►│  Mosquitto   │◄─────────────►│  FastAPI       │
│  (ESP32, EV) │  iot/fleet/*    │  MQTT Broker │               │  Backend       │
│  Simulators  │                 │  :1883       │               │  :8000         │
└──────────────┘                 └──────┬───────┘               └───────┬────────┘
                                        │                               │
                                 ┌──────┴──────┐                 ┌──────┴──────┐
                                 │  Prometheus │                 │  SQLite /   │
                                 │  :9090      │                 │  PostgreSQL │
                                 └──────┬──────┘                 └─────────────┘
                                        │
                                 ┌──────┴──────┐
                                 │  Grafana    │
                                 │  :3000      │
                                 └─────────────┘
```

### 2.1 Data Flow

1. **Devices** connect to the MQTT broker (Mosquitto) and publish registration, heartbeat, and status messages on `iot/fleet/` topics
2. **Backend** (FastAPI) subscribes to device topics, processes registrations, stores data in the database, and publishes OTA/V2G commands
3. **Prometheus** scrapes the backend's `/metrics` endpoint every 10 seconds
4. **Grafana** queries Prometheus for visualisation
5. **Users** interact via the web dashboard (HTTP) or REST API

### 2.2 Technology Stack

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.12 |
| Web Framework | FastAPI | 0.115.12 |
| ASGI Server | Uvicorn | 0.30.0 |
| ORM | SQLAlchemy | 2.0.49 |
| Database | SQLite (dev) / PostgreSQL (prod) | — |
| Validation | Pydantic / Pydantic-Settings | 2.9.0 / 2.14.1 |
| MQTT Client | paho-mqtt | 2.1.0 |
| MQTT Broker | Eclipse Mosquitto | 2 |
| Metrics | Prometheus Client | 0.25.0 |
| Monitoring | Prometheus + Grafana | 2.53.0 / 11.1.0 |
| Templates | Jinja2 | 3.1.6 |
| Frontend | HTMX | 2.0.3 |

---

## 3. Installation & Deployment

### 3.1 Prerequisites

- **Docker** & **Docker Compose v2** (recommended)
- OR: Python 3.12+ with `pip` (standalone)
- Minimum 4 GB RAM (8 GB recommended for full stack)

### 3.2 Quick Start (Docker — Recommended)

```bash
# Start everything (backend + MQTT + Prometheus + Grafana + 5 simulated devices)
docker compose --profile demo up --build -d

# Wait ~30 seconds for all services to become healthy
docker compose ps
```

The following services start:

| Service | URL | Purpose |
|---|---|---|
| Fleet Dashboard | http://localhost:8000 | HTMX web UI |
| API Docs (Swagger) | http://localhost:8000/docs | REST API documentation |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3000 | Dashboards (admin/admin) |
| Mosquitto | localhost:1883 | MQTT broker |

### 3.3 Standalone (Without Docker)

```bash
pip install -r requirements.txt

# Start MQTT broker separately (e.g., Mosquitto on localhost:1883)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3.4 Profiles

| Profile | Services | Command |
|---|---|---|
| `demo` | Backend + Mosquitto + Simulator + Prometheus + Grafana | `--profile demo` |
| `production` | Adds PostgreSQL | `--profile production` |
| `testing` | E2E test runner | `--profile testing` |

### 3.5 Stopping & Cleaning

```bash
# Stop all services
docker compose down

# Stop and remove all volumes (WARNING: deletes database, metrics, and firmware)
docker compose down -v
```

---

## 4. Web Dashboard Usage

Access the dashboard at **http://localhost:8000**.

### 4.1 Header

- **Title:** "Fleet Commander — IoT Device Management Dashboard"
- **MQTT Status:** Shows green "MQTT: Connected" or red "MQTT: Disconnected"
- **Refresh button:** Manually reloads all dashboard data

### 4.2 Stats Cards (Top Row)

| Card | Description |
|---|---|
| **Online Devices** | Number of devices with recent heartbeat (< 60 seconds) |
| **Offline Devices** | Devices that haven't sent a heartbeat in 60+ seconds |
| **OTA In Progress** | Active OTA deployments currently in downloading/applying/verifying state |
| **OTA Success Rate** | Percentage of successful OTA deployments out of total |

Colour coding: green (healthy), red (critical), blue (in-progress).

### 4.3 Device Table

Columns: Device Name, Firmware, Status, Signal Strength, Uptime %, Last Seen, OTA Status.

- Status badges: `● Online` (green), `○ Offline` (red)
- OTA Status badges: `success` (green), `failed` (red), `rolled_back` (yellow), `downloading` (blue), `applying` (purple), `verifying` (yellow), `pending` (grey)
- Checkbox column for selecting specific devices for OTA
- "Select All" checkbox in the header

### 4.4 OTA Deployments Section

- Lists recent OTA deployments (last 20)
- Columns: Deployment ID, Device ID, Status, Retries, Error, Updated
- Status badges use the same colour scheme as the device table

### 4.5 Agent Recommendations Section

Three agent panels auto-refresh every 30 seconds:

| Panel | Content |
|---|---|
| **OTA Campaign Strategist** | Firmware version, online count, canary group size, rollout phases |
| **Fleet Health Monitor** | Health status, critical/warning anomaly count, alert messages |
| **Device Group Manager** | Number of groups found, group names and device counts |

Panel colour coding: blue (OTA), green/orange/red (anomaly), purple (groups).

### 4.6 OTA Trigger Modal

Click **"Trigger OTA Update"** to open the modal:

1. Select firmware version from the dropdown (populated from uploaded firmware)
2. Choose target: "All online devices" (default) or specific devices from the table
3. Click "Trigger Update"
4. Success/failure toast notification appears
5. Dashboard auto-refreshes every 5 seconds to show OTA progress

### 4.7 Auto-Refresh

| Component | Interval |
|---|---|
| Device table & stats | 5 seconds |
| MQTT status badge | 10 seconds |
| Agent recommendation panels | 30 seconds |

---

## 5. Device Management

### 5.1 Device Registration

Devices can register in two ways:

**Automatic (MQTT)** — Device publishes a JSON payload to `iot/fleet/register`:

```json
{
  "device_id": "uuid-or-unique-id",
  "name": "Device-001",
  "firmware_version": "1.0.0",
  "ip_address": "10.0.0.42"
}
```

**Manual (REST API)** — POST to `/devices/register`:

```bash
curl -X POST http://localhost:8000/devices/register \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo-Device-001", "firmware_version": "1.0.0", "ip_address": "10.0.0.42"}'
```

**Behaviour:**
- New device name → creates a new device record with `online` status
- Existing device name → updates the record and sets status back to `online`

### 5.2 Heartbeat Monitoring

Devices send heartbeats to `iot/fleet/{device_id}/heartbeat`:

```json
{
  "uptime_percentage": 99.2,
  "signal_strength": -58
}
```

**EV battery extension** (for V2G-capable devices):

```json
{
  "uptime_percentage": 99.2,
  "signal_strength": -58,
  "soc": 72.3,
  "soh": 99.8,
  "battery_temp": 27.1,
  "plug_status": "connected"
}
```

**Offline detection:** A device is marked `offline` if no heartbeat is received for 60+ seconds.

### 5.3 Remote Device Configuration

Publish to `iot/fleet/{device_id}/command/config`:

```json
{
  "config": {
    "sample_interval": 30,
    "log_level": "debug",
    "power_limit_kw": 5.0
  },
  "timestamp": "2026-05-23T12:00:00Z"
}
```

### 5.4 Listing Devices

```bash
# All devices
curl http://localhost:8000/devices

# Filter by status
curl http://localhost:8000/devices?status=online
curl http://localhost:8000/devices?status=offline
```

---

## 6. OTA Firmware Updates

### 6.1 OTA State Machine

```
pending → downloading → applying → verifying → success
                                         → hash_mismatch → rollback → rolled_back
                              → failed (timeout / max retries)
```

### 6.2 Uploading Firmware

Via Swagger UI (`/docs`) or REST API:

```bash
curl -X POST http://localhost:8000/ota/upload \
  -F "version=2.0.0" \
  -F "file=@/path/to/firmware.bin"
```

The system calculates the **SHA256 hash** of the uploaded binary and stores it for verification during OTA.

**Boundary conditions:**
- Firmware version must be unique (cannot upload the same version twice)
- Maximum file size is limited only by disk space (checked at application level — no explicit cap in code; ensure your reverse proxy or load balancer has appropriate limits)
- Supported file types: any binary format (no restrictions)

### 6.3 Triggering an OTA Update

**Target all online devices:**

```bash
curl -X POST http://localhost:8000/ota/trigger \
  -H "Content-Type: application/json" \
  -d '{"firmware_id": "<FW_ID>", "all_devices": true}'
```

**Target specific devices:**

```bash
curl -X POST http://localhost:8000/ota/trigger \
  -H "Content-Type: application/json" \
  -d '{"firmware_id": "<FW_ID>", "device_ids": ["<DEVICE_ID_1>", "<DEVICE_ID_2>"]}'
```

**Boundary conditions:**
- `firmware_id` must reference an existing firmware record (returns 404 if not found)
- At least one of `device_ids` or `all_devices` must be specified
- With `all_devices=true`, only **currently online** devices are targeted
- Devices must be in `online` status to receive OTA commands
- Returns 404 if no matching devices are found

### 6.4 OTA Status Reporting

Devices report status by publishing to `iot/fleet/{device_id}/status/ota`:

```json
{
  "status": "downloading",
  "deployment_id": "uuid",
  "device_id": "device-uuid",
  "timestamp": 1712345678.0,
  "error": "SHA256 hash mismatch"
}
```

### 6.5 Automatic Rollback

When a device reports `hash_mismatch`:
1. Backend logs the failure
2. Deployment transitions: `hash_mismatch → rollback → rolled_back`
3. Device firmware version reverts to `previous_firmware_version`
4. The dashboard shows a yellow `rolled_back` badge

### 6.6 OTA Timeout & Retry

- Default timeout: **120 seconds** per deployment
- Default max retries: **3**
- After timeout, the backend retries the OTA command (up to `max_retry_count` times)
- After exhausting retries, the deployment is marked `failed`
- Configure via `OTA_TIMEOUT_SECONDS` and `MAX_RETRY_COUNT` environment variables

### 6.7 Listing OTA Status & Firmware

```bash
# View all OTA deployments with counts
curl http://localhost:8000/ota/status

# List uploaded firmware versions
curl http://localhost:8000/ota/firmware
```

---

## 7. V2G (Vehicle-to-Grid) Arbitrage

### 7.1 Overview

The V2G Arbitrage Optimizer schedules EV battery charge/discharge to maximise net revenue, accounting for battery degradation cost. It uses a **heuristic greedy algorithm** that decides charge/discharge/idle per hour over a configurable horizon (default 24 hours).

### 7.2 Battery Degradation Model

**Degradation cost per kWh cycled:**

```
C_deg = replacement_cost × base_fade_per_cycle / capacity × SOH_factor × temp_factor
```

Where:
- `replacement_cost` = $35,000 (configurable via `BATTERY_REPLACEMENT_COST_DOLLARS`)
- `base_fade_per_cycle` = 0.03% per full cycle
- `SOH_factor` = `(80% / SOH)²` when SOH < 80%, otherwise 1.0
- `temp_factor` = Arrhenius model — doubles every 10°C above 25°C

**Boundary conditions:**
- **No discharge allowed** when SOH < 70% (cost becomes infinite)
- Cost rises quadratically as battery health degrades below 80%
- Higher battery temperature accelerates degradation cost
- Temperature floor: minimum 0.5x factor at cold temperatures

### 7.3 Heuristic Optimizer Rules

The optimizer evaluates each time slot and decides:

| Condition | Action |
|---|---|
| Spot price < $0.05/kWh AND SOC < 90% | **Charge** |
| Spot price > degradation cost AND SOC > 25% | **Discharge** |
| Departure approaching AND SOC below target | **Charge** (prioritised) |
| None of the above | **Idle** |

**Boundary conditions:**
- SOC range: 20%–90% (hard limits)
- Departure SOC target: 80% (default)
- Max power: 7.2 kW (Level 2 EVSE)
- No simultaneous charge and discharge
- Vehicle must be connected (`plug_status != "disconnected"`)
- Time step: 60 minutes (configurable)
- Horizon: 24 hours (configurable, up to system memory limits)

### 7.4 V2G API

```bash
# Get V2G dispatch schedule for all devices
curl 'http://localhost:8000/agents/v2g-dispatch'

# Get V2G schedule for specific devices
curl 'http://localhost:8000/agents/v2g-dispatch?device_ids=DEVICE_ID_1&device_ids=DEVICE_ID_2'

# With custom horizon
curl 'http://localhost:8000/agents/v2g-dispatch?horizon_hours=12'
```

**Response fields:**

| Field | Description |
|---|---|
| `agent` | Always "V2G Arbitrage Optimizer" |
| `summary` | Human-readable summary with revenue projection |
| `total_projected_revenue_dollars` | Sum of all slot revenues (can be negative if net cost) |
| `total_deg_cost_dollars` | Accumulated battery degradation cost |
| `schedule` | Array of hourly charge/discharge/idle slots |
| `devices_used` | Number of devices with non-idle schedules |

### 7.5 MQTT V2G Topics

| Topic | Direction | Payload |
|---|---|---|
| `iot/fleet/{id}/command/v2g` | Backend → Device | `{"action": "discharge", "power_kw": 7.2, "duration_minutes": 60}` |
| `iot/fleet/{id}/status/v2g` | Device → Backend | `{"status": "discharging", "power_kw": 7.2, "soc": 45.0}` |

---

## 8. AI Agent Recommendations

### 8.1 Available Agents

The system includes three heuristic agents that run in-backend (no external LLM required). Optional Crew AI LLM integration can be enabled.

| Agent | Endpoint | Purpose | Human Input Required |
|---|---|---|---|
| **OTA Campaign Strategist** | `GET /agents/ota-campaign` | Suggests canary-based phased rollout plan | Yes |
| **Fleet Health Monitor** | `GET /agents/anomaly-check` | Detects weak signals, stuck OTAs, failure spikes, mass offline | No (Level 1) |
| **Device Group Manager** | `GET /agents/device-groups` | Groups devices by firmware version and signal strength | Yes |

### 8.2 OTA Campaign Agent

Produces a rollout plan consisting of:
- **Canary group:** 10% of online fleet (randomly selected)
- **Phase 1:** 30% of fleet
- **Phase 2:** 60% of fleet
- **Phase 3:** 100% of fleet
- **Gate criteria:** Phase failure rate must be < 20% to proceed
- **Risk assessment:** Based on fleet size and online percentage

```bash
curl 'http://localhost:8000/agents/ota-campaign?firmware_version=2.0.0'
```

### 8.3 Anomaly Detection Agent

Checks for four anomaly types:

| Anomaly | Threshold | Severity |
|---|---|---|
| Weak signal | Signal strength > -50 dBm threshold × count | Warning |
| Stuck OTA | OTA in "downloading" for > 5 minutes | Critical |
| Failure spike | > 30% OTA failure rate | Critical |
| Mass offline | > 30% of devices offline | Critical |

```bash
curl 'http://localhost:8000/agents/anomaly-check?notify=true'
```

### 8.4 Device Group Agent

Groups devices by two dimensions:

| Group Type | Criteria | Example |
|---|---|---|
| Firmware version | Same firmware_version | "Firmware 1.0.0 Cohort" |
| Signal strength | Good: > -60 dBm, Moderate: -60 to -80, Poor: < -80 | "Good Signal Group" |

```bash
curl 'http://localhost:8000/agents/device-groups?min_group_size=3'
```

### 8.5 CLI Runner

```bash
# Run all agents
python run_agents.py

# Run specific agent
python run_agents.py --ota --firmware 2.0.0
python run_agents.py --anomaly
python run_agents.py --groups
python run_agents.py --v2g
python run_agents.py --v2g --horizon 12  # Custom horizon

# JSON output
python run_agents.py --json | jq .
```

**CLI options:**

| Flag | Default | Description |
|---|---|---|---|
| `--ota` | — | Run OTA campaign agent only |
| `--anomaly` | — | Run anomaly detection only |
| `--groups` | — | Run device grouping only |
| `--v2g` | — | Run V2G arbitrage dispatch only |
| `--horizon` | 24 | V2G optimization horizon in hours |
| `--firmware` | None | Target firmware version for OTA |
| `--min-group-size` | 3 | Minimum devices per group |
| `--no-notify` | False | Disable Slack notifications |
| `--json` | False | Output as JSON |

---

## 9. MQTT Protocol Reference

### 9.1 Topic Structure

All topics use the prefix `iot/fleet/` and **QoS 1** (at-least-once delivery).

### 9.2 Topics Summary

| Topic | Direction | Payload | When |
|---|---|---|---|
| `iot/fleet/register` | Device → Backend | Registration JSON | On boot and reconnect |
| `iot/fleet/{id}/heartbeat` | Device → Backend | Heartbeat JSON | Periodic (10s default) |
| `iot/fleet/{id}/status/ota` | Device → Backend | OTA status JSON | During OTA lifecycle |
| `iot/fleet/{id}/status/v2g` | Device → Backend | V2G status JSON | During discharge/charge |
| `iot/fleet/{id}/command/ota` | Backend → Device | OTA command JSON | On OTA trigger |
| `iot/fleet/{id}/command/config` | Backend → Device | Config JSON | On config push |
| `iot/fleet/{id}/command/v2g` | Backend → Device | V2G command JSON | On V2G dispatch |

### 9.3 Payload Formats

**Registration (`register`):**
```json
{
  "device_id": "uuid",
  "name": "Device-001",
  "firmware_version": "1.0.0",
  "ip_address": "10.0.0.42"
}
```

**Heartbeat (`heartbeat`):**
```json
{
  "uptime_percentage": 99.2,
  "signal_strength": -58
}
```

**EV heartbeat extension** (if device supports V2G):
```json
{
  "uptime_percentage": 99.2,
  "signal_strength": -58,
  "soc": 72.3,
  "soh": 99.8,
  "battery_temp": 27.1,
  "plug_status": "connected"
}
```

**OTA command (`command/ota`, backend → device):**
```json
{
  "firmware_url": "http://backend:8000/firmware/firmware.bin",
  "sha256_hash": "abcdef1234567890...",
  "timestamp": "2026-05-23T12:00:00Z"
}
```

**OTA status (`status/ota`, device → backend):**
```json
{
  "status": "downloading",
  "deployment_id": "uuid",
  "device_id": "device-uuid",
  "timestamp": 1712345678.0,
  "error": "SHA256 hash mismatch"
}
```

**Config command (`command/config`, backend → device):**
```json
{
  "config": {
    "sample_interval": 30,
    "log_level": "debug"
  },
  "timestamp": "2026-05-23T12:00:00Z"
}
```

**V2G command (`command/v2g`, backend → device):**
```json
{
  "action": "discharge",
  "power_kw": 7.2,
  "duration_minutes": 60,
  "timestamp": "2026-05-23T12:00:00Z"
}
```

**V2G status (`status/v2g`, device → backend):**
```json
{
  "status": "discharging",
  "power_kw": 7.2,
  "soc": 45.0,
  "timestamp": 1712345678.0
}
```

### 9.4 OTA Status Values

| Value | Meaning |
|---|---|
| `downloading` | Device is downloading firmware binary |
| `applying` | Device is flashing the firmware |
| `verifying` | Device is verifying SHA256 hash |
| `success` | OTA completed successfully |
| `hash_mismatch` | SHA256 verification failed |
| `rollback` | Device is reverting to previous firmware |
| `rolled_back` | Rollback completed |
| `failed` | OTA failed (timeout or error) |

### 9.5 V2G Action Values

| Value | Meaning |
|---|---|
| `idle` | No charge/discharge activity |
| `charge` | Battery is charging |
| `discharge` | Battery is discharging (feeding power to grid) |

### 9.6 Plug Status Values

| Value | Meaning |
|---|---|
| `disconnected` | Vehicle not plugged in |
| `connected` | Vehicle plugged in, idle |
| `charging` | Vehicle actively charging |
| `discharging` | Vehicle actively discharging (V2G) |

---

## 10. REST API Reference

### 10.1 Device Endpoints

| Method | Endpoint | Request | Response | Status |
|---|---|---|---|---|
| POST | `/devices/register` | `DeviceRegisterRequest` | `DeviceRegisterResponse` | 201 |
| POST | `/devices/{id}/heartbeat` | `HeartbeatRequest` | `{status, last_seen}` | 200 |
| GET | `/devices` | `?status=online/offline` | `DeviceListResponse` | 200 |

**HeartbeatRequest schema:**
```json
{
  "uptime_percentage": 100.0,
  "signal_strength": 0,
  "soc": null,
  "soh": null,
  "battery_temp": null,
  "plug_status": null
}
```

**DeviceResponse schema:**
```json
{
  "id": "uuid",
  "name": "Device-001",
  "firmware_version": "1.0.0",
  "status": "online",
  "signal_strength": -58,
  "last_seen": "2026-05-23T12:00:00",
  "uptime_percentage": 99.2,
  "ip_address": "10.0.0.42",
  "soc": 80.0,
  "soh": 100.0,
  "battery_temp": 25.0,
  "plug_status": "connected"
}
```

### 10.2 OTA Endpoints

| Method | Endpoint | Request | Response | Status |
|---|---|---|---|---|
| POST | `/ota/upload` | Multipart (version + file) | `FirmwareUploadResponse` | 200 |
| POST | `/ota/trigger` | `OtaTriggerRequest` | `{message, deployment_ids, firmware_version}` | 200 |
| GET | `/ota/status` | — | `OtaStatusResponse` | 200 |
| GET | `/ota/firmware` | — | `List[FirmwareUploadResponse]` | 200 |

**OtaTriggerRequest:**
```json
{
  "firmware_id": "uuid",
  "device_ids": ["uuid1", "uuid2"],
  "all_devices": false
}
```

### 10.3 Agent Endpoints

| Method | Endpoint | Parameters | Description |
|---|---|---|---|
| GET | `/agents/recommendations` | `notify`, `firmware_version`, `min_group_size` | Run all 3 agents |
| GET | `/agents/ota-campaign` | `firmware_version` | OTA rollout plan |
| GET | `/agents/anomaly-check` | `notify` | Fleet health scan |
| GET | `/agents/device-groups` | `min_group_size` | Device groupings |
| GET | `/agents/v2g-dispatch` | `device_ids[]`, `all_devices`, `horizon_hours` | V2G schedule |

### 10.4 Other Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Fleet Dashboard HTML |
| GET | `/metrics` | Prometheus metrics (Prometheus text format) |
| GET | `/firmware/{filename}` | Serve firmware binary files |

### 10.5 Error Responses

All endpoints return standard HTTP errors:

| Status | Meaning |
|---|---|
| 400 | Bad request (e.g., missing required fields) |
| 404 | Resource not found (device, firmware, etc.) |
| 422 | Validation error (Pydantic schema validation) |
| 500 | Internal server error |

Error body:
```json
{
  "detail": "Error description message"
}
```

---

## 11. Prometheus Metrics

All metrics are exposed at `GET /metrics` in Prometheus text format.

### 11.1 Metric Reference

| Metric | Type | Labels | Description |
|---|---|---|---|
| `fleet_active_devices` | Gauge | — | Currently online devices |
| `fleet_total_devices` | Gauge | — | Total registered devices (lifetime) |
| `fleet_ota_deployments_total` | Counter | `status` | OTA deployment attempts by status |
| `fleet_ota_in_progress` | Gauge | — | Active OTA deployments |
| `fleet_api_request_latency_seconds` | Histogram | `method`, `endpoint` | API latency distribution |
| `fleet_mqtt_messages_published_total` | Counter | `topic` | MQTT published messages by topic |
| `fleet_mqtt_messages_received_total` | Counter | `topic` | MQTT received messages by topic |
| `fleet_v2g_active_discharges` | Gauge | — | Devices currently discharging |
| `fleet_v2g_projected_revenue_dollars` | Gauge | — | Projected V2G arbitrage revenue |
| `fleet_battery_degradation_cost_dollars` | Gauge | — | Accumulated battery degradation cost |
| `fleet_device_soc` | Gauge | `device` | Per-device state of charge |

**Boundary conditions:**
- Gauge values are **in-memory** and reset to zero on backend restart
- Counter values also reset on restart (no persistence)
- Histogram buckets: 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s
- The `/metrics` endpoint itself is instrumented by the latency middleware (visible in the histogram)

### 11.2 Scraping Configuration

Grafana scrapes every 10 seconds from `backend:8000/metrics`. Prometheus retention is 7 days (configurable via `--storage.tsdb.retention.time`).

---

## 12. Grafana Observability

### 12.1 Access

- **URL:** http://localhost:3000
- **Credentials:** admin / admin

### 12.2 Pre-Built Dashboard: "Fleet Commander Overview"

The dashboard is auto-provisioned on startup with the following panels:

| Panel | Type | Query | Description |
|---|---|---|---|
| Active Devices | Stat | `fleet_active_devices` | Online device count (green ≥ 1, red = 0) |
| Total Devices | Stat | `fleet_total_devices` | Total registered device count |
| OTA In Progress | Stat | `fleet_ota_in_progress` | Active OTA count (green=0, orange≥1, red≥5) |
| OTA Success Rate | Stat | `rate(fleet_ota_deployments_total{status="success"}[5m]) / rate(fleet_ota_deployments_total[5m]) * 100` | Success % (red<50, orange<80, green≥80) |
| API Request Latency (P95) | Timeseries | `histogram_quantile(0.95, sum(rate(fleet_api_request_latency_seconds_bucket[5m])) by (le))` | P95 latency over time |
| OTA Deployments by Status | Pie Chart | `fleet_ota_deployments_total` | Distribution by status label |
| MQTT Message Throughput | Timeseries | `rate(fleet_mqtt_messages_published_total[5m])` + `rate(fleet_mqtt_messages_received_total[5m])` | Published vs received per second |
| Device Uptime Distribution | Bar Gauge | `(fleet_active_devices / fleet_total_devices) * 100` | Active % (0–100%) |
| V2G Active Discharges | Stat | `fleet_v2g_active_discharges` | Count of discharging devices |
| Projected V2G Revenue | Stat | `fleet_v2g_projected_revenue_dollars` | Revenue in USD |
| Battery Degradation Cost | Stat | `fleet_battery_degradation_cost_dollars` | Wear cost in USD |
| Spot Price vs Degradation Cost | Timeseries | (query via V2G optimizer) | Price comparison |
| Fleet SOC over Time | Timeseries | `fleet_device_soc` | Per-device SOC trend |

### 12.3 Customising Grafana

1. Log in to Grafana (admin/admin)
2. Navigate to the Fleet Commander Overview dashboard
3. Click the gear icon → "Make editable" to modify panels
4. Add new panels, adjust thresholds, change visualisation types

Changes are **not persisted** across container restarts unless you create a new dashboard (instead of modifying the provisioned one). To persist custom dashboards, save them to the `docker/grafana/dashboards/` directory.

---

## 13. Configuration Reference

### 13.1 Backend Environment Variables

| Variable | Default | Description | Valid Range / Boundary |
|---|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/fleet.db` | Database connection string | Must be a valid SQLAlchemy async DB URL |
| `MQTT_BROKER_HOST` | `localhost` | MQTT broker hostname | Any resolvable hostname or IP |
| `MQTT_BROKER_PORT` | `1883` | MQTT broker port | 1–65535 |
| `MQTT_USERNAME` | — | MQTT authentication username | Optional; must be set with PASSWORD |
| `MQTT_PASSWORD` | — | MQTT authentication password | Optional; must be set with USERNAME |
| `HOST` | `0.0.0.0` | Backend bind address | Valid IP address |
| `PORT` | `8000` | Backend HTTP port | 1–65535 |
| `LOG_LEVEL` | `INFO` | Logging verbosity | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `OTA_TIMEOUT_SECONDS` | `120` | OTA deployment timeout | ≥ 10 seconds |
| `FIRMWARE_STORAGE_PATH` | `./firmware` | Firmware file storage directory | Must be writable; absolute or relative path |
| `MAX_RETRY_COUNT` | `3` | Maximum OTA retry attempts | ≥ 0 (0 = no retries) |
| `BATTERY_REPLACEMENT_COST_DOLLARS` | `35000.0` | Battery replacement cost | > 0 |
| `BATTERY_CAPACITY_KWH` | `60.0` | Battery capacity in kWh | > 0 |
| `SOH_MIN_DISCHARGE` | `0.7` | Minimum SOH for discharge | 0.0–1.0 |
| `SOH_DEG_THRESHOLD` | `0.8` | SOH degradation threshold | 0.0–1.0 |
| `SPOT_PRICE_URL` | — | Spot price API URL | Empty = uses mock prices |
| `V2G_HORIZON_HOURS` | `24` | V2G optimisation horizon | 1–168 (7 days) |
| `V2G_TIME_STEP_MINUTES` | `60` | V2G optimisation time step | Must evenly divide 60 |
| `PROMETHEUS_MULTIPROC_DIR` | `/tmp` | Prometheus multiproc directory | Must be writable |

### 13.2 Simulator Environment Variables

| Variable | Default | Description | Valid Range |
|---|---|---|---|
| `SIMULATOR_DEVICE_COUNT` | `5` | Number of simulated devices | 1–1000 |
| `SIMULATOR_HEARTBEAT_INTERVAL` | `10` | Heartbeat interval in seconds | ≥ 1 |
| `SIMULATOR_OTA_FAILURE_RATE` | `0.2` | OTA failure probability | 0.0–1.0 |

### 13.3 Docker Compose Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/fleet.db` | Override database URL |
| `SIMULATOR_DEVICE_COUNT` | `5` | Override for demo profile |

### 13.4 Configuration File

Copy `.env.example` to `.env` and modify as needed:

```bash
cp .env.example .env
```

The backend auto-loads `.env` files from the working directory.

---

## 14. Boundary Conditions & Limitations

### 14.1 Scalability

| Aspect | Current Limit | Notes |
|---|---|---|
| Devices (SQLite) | ~1000 concurrent | SQLite is single-writer; beyond this, use PostgreSQL |
| Devices (PostgreSQL) | ~10,000+ | Scales with hardware; PG tuned for concurrent writes |
| MQTT message rate | Broker-dependent | Mosquitto default handles ~10K msg/sec |
| Prometheus retention | 7 days | Configurable via `--storage.tsdb.retention.time` |
| Concurrent OTA | Unlimited | Each OTA is an async task; CPU-bound on large fleets |
| Database size | SQLite: ~10 GB | Beyond that, migrate to PostgreSQL |
| Firmware file size | No code limit | Set limits at reverse proxy level |

### 14.2 Known Limitations

| Limitation | Details | Workaround |
|---|---|---|
| Metrics reset on restart | Prometheus Gauge/Counter values are in-memory | Use persistent Prometheus volume; metrics re-populate as devices re-register |
| No authentication built-in | Dev mode uses no MQTT or API auth | See SECURITY.md for production hardening |
| Single MQTT broker | Not clustered out of the box | Use MQTT bridge or cluster config for HA |
| Health check on restart | No warm-up period for OTA retry timers | First OTA after restart uses fresh counters |
| V2G heuristic (not MILP) | Greedy algorithm may miss global optimum | Planned MILP upgrade in SCALING.md |
| Mock spot prices | Not real market data | Set `SPOT_PRICE_URL` for real prices |
| Simulator devices are virtual | For testing only | Replace with real ESP32 hardware |
| Database migrations | No Alembic auto-migration | Manual schema sync required for version changes |

### 14.3 Operational Boundaries

| Condition | Behaviour |
|---|---|
| Device sends heartbeat for unknown device | 404 returned, device not created |
| OTA triggered for offline device | Device excluded from target list |
| Firmware version already exists | Database UNIQUE constraint violation error |
| MQTT broker unavailable | Backend starts, logs warning, retries with backoff |
| Disk full on firmware storage | File write fails, error returned to caller |
| Backend restarts during OTA | In-progress OTA deployments remain in DB; timeout watcher re-triggers |
| Multiple backends (horizontal scale) | MQTT messages delivered to one subscriber; use shared DB |
| Grafana not reachable | Backend operates independently; metrics still collected |

### 14.4 Database Schema

Five tables: `devices`, `firmware`, `ota_deployments`, `v2g_schedules`.

- Devices have a **one-to-many** relationship with OTA deployments and V2G schedules
- Firmware records are independent (no FK to devices)
- No cascade deletes configured (manual cleanup required)
- UUID primary keys across all tables

---

## 15. Troubleshooting

### 15.1 Dashboard Shows No Devices

**Symptoms:** Dashboard shows 0 devices, stats show 0.

**Causes & fixes:**

1. **Startup race condition** — Simulator registered before backend subscribed to MQTT:
   ```bash
   docker compose --profile demo down -v && docker compose --profile demo up --build -d
   ```

2. **Simulator not running** — Check with `docker compose ps`, ensure `simulator` is listed and not restarting

3. **Backend logs show no MQTT activity:**
   ```bash
   docker compose logs backend | grep MQTT
   ```
   Should show "Connected to MQTT broker" and "MQTT auto-registered device: ..."

### 15.2 Grafana Shows "No Data"

**Symptoms:** Grafana panels display "No data" or empty.

**Causes & fixes:**

1. **Prometheus not scraping** — Verify targets:
   ```bash
   curl http://localhost:9090/api/v1/targets
   ```
   Check `fleet-commander` target is `UP`

2. **Check metrics are exposed:**
   ```bash
   curl http://localhost:8000/metrics | grep fleet_
   ```

3. **Check Grafana datasource:**
   ```bash
   curl -u admin:admin http://localhost:3000/api/datasources
   ```
   Should show Prometheus datasource with uid `prometheus`

4. **Restart from scratch:**
   ```bash
   docker compose --profile demo down -v && docker compose --profile demo up --build -d
   ```

### 15.3 OTA Trigger Fails

**Symptoms:** OTA trigger returns error or devices don't respond.

**Causes & fixes:**

1. **Firmware ID not found** — List available firmware:
   ```bash
   curl http://localhost:8000/ota/firmware
   ```

2. **No online devices** — Check device status:
   ```bash
   curl http://localhost:8000/devices?status=online
   ```

3. **MQTT not connected** — Check backend logs:
   ```bash
   docker compose logs backend | grep -i mqtt
   ```

### 15.4 V2G Dispatch Returns Empty Schedule

**Symptoms:** V2G API returns empty schedule.

**Causes & fixes:**

1. **No EV devices** — Simulator marks first 3 devices as EVs. Check heartbeats include SOC/SOH fields.

2. **Devices disconnected** — Check plug_status in device response.

3. **SOH too low** — `soh_min_discharge` default is 70%.

### 15.5 Backend Won't Start

**Symptoms:** Container exits immediately.

**Causes & fixes:**

1. **Port conflict** — Port 8000 already in use:
   ```bash
   netstat -ano | findstr :8000
   ```

2. **Database error** — Check permissions on data directory

3. **MQTT connection failure —** Mosquitto not healthy yet:
   ```bash
   docker compose logs mosquitto
   ```

### 15.6 Logs & Debugging

```bash
# All services
docker compose logs --tail=50 -f

# Specific service
docker compose logs --tail=50 backend
docker compose logs --tail=50 simulator
docker compose logs --tail=50 mosquitto

# Increase log verbosity
LOG_LEVEL=DEBUG docker compose --profile demo up -d
```

---

## 16. Security Considerations

### 16.1 Default (Development) Configuration

**⚠  WARNING:** The default configuration uses **no authentication** and is intended for **development/testing only**.

- MQTT: `allow_anonymous true` — no authentication
- API: No authentication middleware
- Grafana: Default admin/admin credentials
- No TLS encryption

### 16.2 Production Hardening

For production deployment, see the full `SECURITY.md` file. Key measures include:

| Area | Recommended Action |
|---|---|
| MQTT TLS | Enable SSL/TLS on port 8883 |
| MQTT Auth | Set `MQTT_USERNAME` and `MQTT_PASSWORD` |
| API Security | Add JWT authentication middleware |
| Grafana | Change default admin password |
| Secrets | Use Docker secrets or vault for passwords |
| Network | Use Docker internal network; restrict port exposure |
| CORS | Configure CORS origins in production |
| Rate Limiting | Add reverse proxy (nginx) with rate limits |

### 16.3 OTA Security

- Firmware integrity verified via SHA256 hash
- Devices verify hash after download before applying
- Rollback mechanism prevents bricking on failed update

### 16.4 V2G Security

- Degradation cost acts as economic safety check
- Battery SOH limits prevent over-discharge
- Temperature monitoring prevents thermal runaway

---

## 17. Production Deployment

### 17.1 PostgreSQL Migration

```bash
docker compose --profile production up -d
```

Set `DATABASE_URL` to a PostgreSQL connection string:
```
DATABASE_URL=postgresql+psycopg2://fleet:fleet_password@postgres:5432/fleet
```

### 17.2 Horizontal Scaling

```bash
# Scale backend instances
docker compose up -d --scale backend=3
```

**Important:** Multiple backend instances share the same MQTT topic subscriptions. With MQTT shared subscriptions, each message is delivered to one subscriber. Use a shared database (PostgreSQL) for consistency.

### 17.3 Reverse Proxy (Recommended)

Place behind nginx or Traefik for:
- TLS termination
- Rate limiting
- Request size limits
- Path-based routing

Example nginx config snippet:
```nginx
server {
    listen 443 ssl;
    server_name fleet.example.com;

    client_max_body_size 100M;

    location / {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

### 17.4 Persistent Storage

| Data | Storage | Volume | Notes |
|---|---|---|---|
| Database | SQLite/PostgreSQL | `sqlite_data` / `pgdata` | Critical — backup regularly |
| Firmware binaries | Filesystem | `firmware_data` | S3-compatible for production |
| Prometheus TSDB | Filesystem | `prometheus_data` | 7 days default retention |
| Grafana DB | SQLite | `grafana_data` | Dashboard settings, users |

---

## 18. ESP32 / IoT Device Connection

### 18.1 Overview

Real ESP32 devices can connect to Fleet Commander using MQTT. A complete Arduino sketch is provided in `ESP32_GUIDE.md`.

### 18.2 Connection Steps

1. **Network:** ESP32 connects to WiFi, then to the MQTT broker (Mosquitto on port 1883)
2. **Subscribe:** Device subscribes to `iot/fleet/{id}/command/ota` and `iot/fleet/{id}/command/config`
3. **Register:** Device publishes to `iot/fleet/register` with its name and firmware version
4. **Heartbeat loop:** Every 10–15 seconds, publish uptime and signal strength
5. **OTA handler:** On receiving an OTA command, download firmware, verify SHA256, apply using ESP32 `Update` class, report status

### 18.3 Required Libraries

- `PubSubClient` by Nick O'Leary (MQTT)
- `ArduinoJson` by Benoit Blanchon (JSON parsing)
- `WiFi` (built-in ESP32)
- `HTTPClient` (built-in, for firmware download)
- `Update` (built-in, for OTA flashing)

### 18.4 MQTT Broker Address

From an ESP32 on the same network, use the Docker host's LAN IP address (not `localhost`).

### 18.5 ESP32-Specific Notes

- Device ID derived from MAC address in the example sketch
- Real OTA uses the ESP32 `Update` class for safe flashing
- Re-register on MQTT reconnect so Prometheus gauges stay accurate
- Set `MQTT_KEEPALIVE` to 60 seconds (matches server configuration)

See `ESP32_GUIDE.md` for the complete Arduino sketch with real OTA flashing, battery simulation, and config handling.

---

## Appendix A: Quick Reference Card

```bash
# Start demo
docker compose --profile demo up --build -d

# List devices
curl http://localhost:8000/devices

# Check metrics
curl http://localhost:8000/metrics | grep fleet_

# Upload firmware
curl -X POST http://localhost:8000/ota/upload -F "version=2.0.0" -F "file=@firmware.bin"

# Trigger OTA
curl -X POST http://localhost:8000/ota/trigger \
  -H "Content-Type: application/json" \
  -d '{"firmware_id":"<FW_ID>","all_devices":true}'

# V2G dispatch
curl http://localhost:8000/agents/v2g-dispatch

# Agent recommendations
curl http://localhost:8000/agents/recommendations?notify=false

# Grafana
open http://localhost:3000  # admin/admin

# Stop everything
docker compose --profile demo down -v
```

## Appendix B: File Structure

```
fleet-management/
├── app/                       # Backend application
│   ├── main.py                # Entry point, lifespan, MQTT handlers
│   ├── config.py              # Pydantic settings (env-based)
│   ├── database.py            # SQLAlchemy async engine + session
│   ├── models.py              # ORM models (5 tables)
│   ├── schemas.py             # Pydantic request/response schemas
│   ├── mqtt_client.py         # MQTT v5 client wrapper
│   ├── ota_manager.py         # OTA state machine + timeout watcher
│   ├── metrics.py             # Prometheus metric definitions
│   ├── v2g_optimizer.py       # V2G arbitrage optimizer
│   ├── routers/               # API route handlers
│   │   ├── devices.py         # Device CRUD endpoints
│   │   ├── ota.py             # OTA upload/trigger/status
│   │   └── dashboard.py       # Fleet dashboard HTML
│   └── templates/
│       └── dashboard.html     # HTMX-powered dashboard (431 lines)
├── agents/                    # Phase 1 AI agents
│   ├── tools.py               # HTTP-based CLI tools
│   ├── async_tools.py         # Async DB-backed tools
│   ├── phase1_crew.py         # Agent definitions + Crew AI
│   └── routers.py             # /agents/* API endpoints
├── simulator/
│   └── simulator.py           # Virtual device simulator
├── tests/
│   ├── test_e2e.py            # End-to-end integration tests
│   └── test_v2g.py            # V2G optimizer unit tests
├── docker/
│   ├── prometheus/prometheus.yml
│   ├── grafana/provisioning/  # Auto-provisioned dashboards
│   │   ├── dashboards/
│   │   └── datasources/
│   └── mosquitto/mosquitto.conf
├── docker-compose.yml         # Multi-service orchestration
├── Dockerfile                 # Backend container
├── Dockerfile.simulator       # Simulator container
├── Dockerfile.tests           # Test runner container
├── requirements.txt           # Python dependencies
├── run_agents.py              # CLI agent runner
├── .env.example               # Config template
├── CUDO.md                    # This file
├── ESP32_GUIDE.md             # ESP32 connection guide
├── SCALING.md                 # Scaling strategy
├── AI_AGENTS.md               # Agent architecture
├── SECURITY.md                # Security hardening guide
├── DEMO_GUIDE.md              # Presentation scripts
└── README.md                  # Project overview
```

---

*Document generated from code version 1.0.0 — Fleet Commander IoT Device Management Module*
