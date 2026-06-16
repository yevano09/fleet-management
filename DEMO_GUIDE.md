# Fleet Commander — Demo Guide

Three presentation styles for showcasing the Fleet Commander IoT device management module.

---

## Short Style (~2 minutes) — Elevator Pitch

**Goal:** Show the bulk OTA update and a rollback in real time.

### Steps

1. **Start the environment** (pre-staged):
   ```bash
   docker compose --profile demo up -d
   ```

2. **Open the Fleet Dashboard** at http://localhost:8000

3. **Point out** the device table showing 5 online devices (Device-001 through Device-005) with firmware version `1.0.0`, green status badges, and varying signal strength.

4. **Trigger a bulk OTA:**
   - Click **"Trigger OTA Update"**
   - Select a firmware (e.g., `2.0.0` — pre-uploaded)
   - Keep "All online devices" checked
   - Click **"Trigger Update"**

5. **Watch the dashboard auto-refresh** (every 5 seconds). Within 10–15 seconds:
   - Some devices show `success` (green badge)
   - At least one shows `rolled_back` (yellow badge) — the 20% failure rate simulates a hash mismatch
   - The rolled-back device stayed on firmware `1.0.0`

6. **Call out:** *"That's it. In under 30 seconds we pushed a firmware update to our entire fleet, and the system automatically handled a failed device with a safe rollback. No manual intervention needed."*

### Key Talking Points

- Device auto-registration on first heartbeat
- MQTT-based command and control
- Automatic rollback on hash mismatch
- Real-time dashboard visibility

---

## Detailed Style (~10 minutes) — Architecture Deep Dive

**Goal:** Explain the backend architecture, MQTT topics, and Grafana observability.

### 1. System Overview (2 min)

Open the architecture diagram (README.md) and explain the data flow:

- **Devices** talk to **Mosquitto** (MQTT broker) over `iot/fleet/` topics
- **Backend** (FastAPI) subscribes to device status topics, publishes OTA commands
- **Prometheus** scrapes `/metrics` from the backend every 10 seconds
- **Grafana** visualizes fleet health from Prometheus data

### 2. MQTT Topic Structure (2 min)

Open `app/mqtt_client.py` and walk through:

| Topic | Purpose |
|---|---|
| `iot/fleet/{id}/command/ota` | Backend publishes firmware URL + SHA256 |
| `iot/fleet/{id}/status/ota` | Device reports download→apply→verify→success/fail |
| `iot/fleet/{id}/heartbeat` | Periodic uptime + signal strength |
| `iot/fleet/register` | Auto-registration on first connect |

Show the subscription setup in `mqtt_client.py:_on_connect()`.

### 3. OTA State Machine & Rollback (3 min)

Open `app/ota_manager.py:OtaStateMachine` and trace the flow:

```
pending → downloading → applying → verifying → success
                                         → hash_mismatch → rollback → rolled_back
```

Demo on the dashboard:
1. Upload a firmware binary via Swagger at `/docs` → `/ota/upload`
2. Trigger via dashboard or API
3. Watch the state transitions in real-time
4. When a device reports `hash_mismatch`, the state machine:
   - Logs the error
   - Updates the deployment to `rolled_back`
   - Reverts the device's `firmware_version` to `previous_firmware_version`

### 4. Grafana Observability (3 min)

Open Grafana at http://localhost:3000 (admin/admin).

Point out each panel:
- **Active / Total Devices** — gauge showing online count
- **OTA In Progress** — current deployments
- **OTA Success Rate** — `rate()` query showing success percentage
- **API Request Latency (P95)** — histogram quantile as timeseries
- **OTA Deployments by Status** — pie chart by deployment status
- **MQTT Message Throughput** — rate of published/received messages
- **Online Devices** — bar gauge of active percentage

Run an OTA trigger and watch the graphs update live.

---

## All-Features Step-by-Step (~20 minutes) — Exhaustive Walkthrough

**Goal:** Manually trigger every feature.

### Prerequisites

```bash
docker compose --profile demo up -d
# Wait for all services to be healthy
docker compose ps
```

### 1. Device Auto-Registration

```bash
# Register a device manually via API
curl -X POST http://localhost:8000/devices/register \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo-Device-001", "firmware_version": "1.0.0", "ip_address": "10.0.0.42"}'
```

Verify on the dashboard — it appears in the device table with status `Online`.

### 2. Heartbeat Updates

```bash
curl -X POST http://localhost:8000/devices/{DEVICE_ID}/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"uptime_percentage": 99.2, "signal_strength": -58}'
```

The dashboard updates the uptime % and signal strength columns.

### 3. Remote Config Push (via MQTT)

Check the simulator logs:
```bash
docker compose logs simulator
```

When the backend publishes to `iot/fleet/{id}/command/config`, the simulator logs: `"Received remote config: {...}"`.

### 4. Successful OTA

```bash
# Upload firmware
curl -X POST http://localhost:8000/ota/upload \
  -F "version=2.0.0" \
  -F "file=@/path/to/firmware.bin"

# Get the firmware ID from response, then:
curl -X POST http://localhost:8000/ota/trigger \
  -H "Content-Type: application/json" \
  -d '{"firmware_id": "<FW_ID>", "device_ids": ["<DEVICE_ID>"]}'
```

On the dashboard, watch the device's OTA status column go through: `downloading → applying → verifying → success`.

### 5. Failed OTA with Rollback

With the simulator's `SIMULATOR_OTA_FAILURE_RATE=0.2`, approximately 1 in 5 OTA attempts will fail. When it does:

1. Device reports `hash_mismatch`
2. Backend sets deployment status to `rolled_back`
3. Device firmware version reverts to `previous_firmware_version`
4. Dashboard shows a yellow `rolled_back` badge

Force a failure by adjusting the env var:
```bash
SIMULATOR_OTA_FAILURE_RATE=1.0 docker compose --profile demo up -d simulator
```

### 6. Offline Queueing

Stop the simulator to simulate devices going offline:
```bash
docker compose stop simulator
```

After 60 seconds, the device appears `Offline` on the dashboard. When the simulator comes back, it re-registers and the status returns to `Online`.

### 7. Review Prometheus Metrics

```bash
# Metrics exposed at /metrics (no trailing slash needed)
curl -s http://localhost:8000/metrics | grep fleet_
```

Sample output:
```
# HELP fleet_active_devices Number of currently online devices
# TYPE fleet_active_devices gauge
fleet_active_devices 5.0
# HELP fleet_ota_deployments_total Total OTA deployment attempts
# TYPE fleet_ota_deployments_total counter
fleet_ota_deployments_total{status="success"} 4.0
fleet_ota_deployments_total{status="triggered"} 5.0
```

### 8. Grafana Dashboard

Navigate to http://localhost:3000 (admin/admin). Open the "Fleet Commander Overview" dashboard. Run multiple OTA triggers and watch:
- The **Active Devices** stat update
- **OTA Deployments by Status** pie chart reflect successes vs failures
- **API Latency** show request duration histograms
- **MQTT Message Throughput** graph spike with each OTA command

### 9. Agent Recommendations (Phase 1)

The dashboard now includes three AI agent panels at the bottom, auto-refreshing every 30 seconds.

#### OTA Campaign Agent

After uploading a firmware:
```bash
curl -X POST http://localhost:8000/ota/upload \
  -F "version=2.0.0" \
  -F "file=@/path/to/firmware.bin"
```

The OTA Campaign panel shows:
- **Canary group** size and device IDs (10% of online fleet)
- **Rollout phases** with device counts and gate criteria
- **Risk assessment** level and recommendation summary

The agent recommends a phased rollout: canary → Phase 1 (30%) → Phase 2 (60%) → Phase 3 (100%), with pass/fail gates between each phase.

#### Anomaly Detection Agent

The Fleet Health panel shows:
- **Status** (healthy / anomalies found) with color coding
- **Critical alerts** (stuck OTAs, failure spikes, mass offline)
- **Warning alerts** (weak signals, degrading devices)

To see anomalies in action, stop a device or trigger a high-failure-rate OTA:
```bash
curl -X POST http://localhost:8000/ota/trigger \
  -H "Content-Type: application/json" \
  -d '{"firmware_id": "<FW_ID>", "all_devices": true}'
```
After OTA failures, the anomaly panel will show failure rate spikes.

#### Device Group Manager

The Device Groups panel shows:
- **Firmware version cohorts** (e.g., "Firmware 1.0.0 Cohort" — 5 devices)
- **Signal strength buckets** (Good / Moderate / Poor)
- Each group includes device count, device IDs, and rationale

Use these groups for targeted OTA rollouts or health comparisons.

#### REST API

```bash
# Full recommendation report (JSON)
curl http://localhost:8000/agents/recommendations?notify=false

# OTA campaign for a specific firmware
curl 'http://localhost:8000/agents/ota-campaign?firmware_version=2.0.0'

# Anomaly check only
curl 'http://localhost:8000/agents/anomaly-check?notify=false'

# Device groupings
curl 'http://localhost:8000/agents/device-groups'
```

### 10. V2G Arbitrage with Battery Degradation Pricing

The system includes a V2G (Vehicle-to-Grid) arbitrage optimizer that schedules EV battery charge/discharge to maximise net revenue, accounting for battery degradation cost.

#### Battery Degradation Model

The degradation cost per kWh cycled is:

`C_deg = replacement_cost × base_fade_per_cycle / capacity × SOH_factor × temp_factor`

- **SOH_factor**: `(80% / SOH)²` when SOH < 80%, otherwise 1.0
- **Temp_factor**: Arrhenius model — doubles every 10°C above 25°C
- **No discharge allowed** when SOH < 70%
- Default replacement cost: $35,000 (configurable via `BATTERY_REPLACEMENT_COST_DOLLARS`)

#### Heuristic Optimizer

The optimizer generates a 24-horizon schedule (configurable via `V2G_HORIZON_HOURS`) that decides charge/discharge/idle per hour:

- **Charge** when spot price < $0.05/kWh (cheap)
- **Discharge** when spot price > degradation cost AND SOC > 25%
- **Prioritise pre-departure charging** to meet departure SOC target
- Respects SOC bounds (20%–90%) and no simultaneous charge/discharge

#### V2G API

```bash
# Get V2G dispatch schedule for all devices
curl 'http://localhost:8000/agents/v2g-dispatch'

# Get V2G schedule for specific devices
curl 'http://localhost:8000/agents/v2g-dispatch?device_ids=DEVICE_ID_1&device_ids=DEVICE_ID_2'

# With custom horizon
curl 'http://localhost:8000/agents/v2g-dispatch?horizon_hours=12'
```

Sample response (truncated):
```json
{
  "agent": "V2G Arbitrage Optimizer",
  "summary": "V2G arbitrage schedule generated for 1 device(s). Projected revenue: $12.45, degradation cost: $0.08, net: $12.37.",
  "total_projected_revenue_dollars": 12.45,
  "total_deg_cost_dollars": 0.08,
  "schedule": [
    {
      "start_time": "2025-01-15T03:00:00",
      "end_time": "2025-01-15T04:00:00",
      "action": "charge",
      "power_kw": 7.2,
      "energy_kwh": 7.2,
      "spot_price_per_kwh": 0.04,
      "deg_cost_per_kwh": 0.0,
      "net_revenue_dollars": -0.29
    },
    {
      "start_time": "2025-01-15T18:00:00",
      "end_time": "2025-01-15T19:00:00",
      "action": "discharge",
      "power_kw": 7.2,
      "energy_kwh": 7.2,
      "spot_price_per_kwh": 0.35,
      "deg_cost_per_kwh": 0.0105,
      "net_revenue_dollars": 2.44
    }
  ],
  "devices_used": 1
}
```

#### MQTT V2G Topics

| Topic | Direction | Payload |
|---|---|---|
| `iot/fleet/{id}/command/v2g` | Backend → Device | `{"action": "discharge", "power_kw": 7.2, "duration_minutes": 60}` |
| `iot/fleet/{id}/status/v2g` | Device → Backend | `{"status": "discharging", "power_kw": 7.2, "soc": 45.0}` |

Heartbeats now include EV battery fields:
```json
{
  "uptime_percentage": 99.1,
  "signal_strength": -58,
  "soc": 72.3,
  "soh": 99.8,
  "battery_temp": 27.1,
  "plug_status": "connected"
}
```

#### Demoing V2G

1. Start the system: `docker compose --profile demo up -d`
2. Wait for devices to register (first 3 are EVs with battery simulation)
3. Call the V2G optimizer:
   ```bash
   curl -s http://localhost:8000/agents/v2g-dispatch | python -m json.tool
   ```
4. Check Grafana at http://localhost:3000 — new V2G panels show:
   - **V2G Active Discharges** — count of discharging devices
   - **Projected V2G Revenue** — total arbitrage revenue
   - **Battery Degradation Cost** — accumulated wear cost
   - **Spot Price vs Degradation Cost** — timeseries comparison
   - **Fleet SOC over Time** — per-device state of charge
5. Check Prometheus metrics:
   ```bash
   curl -s http://localhost:8000/metrics | grep -E "v2g|battery_deg"
   ```
6. Simulator logs show V2G command execution:
   ```bash
   docker compose logs simulator | grep -i v2g
   ```

#### Scaling Strategy

See `SCALING.md` for details on:
- **ML models**: LSTM/Transformer for spot price forecasting
- **Protocols**: OCPP 2.0.1 for EVSE integration, OpenADR 2.0b for DERMS
- **Database**: TimescaleDB hypertables for time-series battery/SOC data
- **Optimization**: MILP via PuLP/OR-Tools with stochastic price scenarios

### 11. Device Onboarding Agent

The Device Onboarding Agent guides you through introducing a new device to the fleet. It checks for naming conflicts, recommends the optimal firmware, registers the device, pushes initial MQTT configuration, and verifies it comes online.

#### How It Works

```
Operator submits device name
        │
        ▼
Device Onboarding Agent
  1. Check name/MQTT ID conflicts  ──► Database lookup
  2. Recommend latest firmware      ──► Firmware list
  3. Generate initial config        ──► heartbeat interval, OTA poll
  4. Register device (if approved)  ──► INSERT Device + metrics
  5. Push MQTT config               ──► publish_remote_config
  6. Verify first heartbeat         ──► Check last_seen
        │
        ▼
Onboarding Report (JSON → CLI / Dashboard UI)
```

#### From the Dashboard

1. Click **"Onboard Device"** button (green) in the toolbar
2. Enter a device name (required), optionally specify firmware, IP, MQTT client ID
3. Check "Auto-register" to skip the review step
4. Click **"Onboard Device"**
5. The result panel shows:
   - **Conflicts** if the name or MQTT ID is already in use
   - **Onboarding plan** if in review mode (recommendation only)
   - **Success** with device ID, firmware, verification status if registered

#### Via REST API

```bash
# Recommendation mode (read-only plan)
curl 'http://localhost:8000/agents/onboarding?name=Sensor-042&firmware_version=2.0.0'

# Auto-register mode (creates device + pushes config)
curl 'http://localhost:8000/agents/onboarding?name=Sensor-042&auto_register=true'

# With all parameters
curl 'http://localhost:8000/agents/onboarding?name=Sensor-042&firmware_version=2.0.0&ip_address=10.0.0.100&mqtt_client_id=esp32-s042&auto_register=true'
```

Sample response:
```json
{
  "agent": "Device Onboarding Agent",
  "type": "device_onboarding",
  "summary": "Device 'Sensor-042' onboarded successfully.",
  "details": {
    "onboarding_possible": true,
    "conflicts": [],
    "recommended_firmware": {"id": "...", "version": "2.0.0"},
    "initial_config": {
      "heartbeat_interval_seconds": 10,
      "ota_poll_interval_seconds": 60,
      "log_level": "INFO"
    },
    "device": {
      "id": "a1b2c3d4-...",
      "name": "Sensor-042",
      "firmware_version": "2.0.0",
      "status": "online",
      "ip_address": "10.0.0.100"
    },
    "registration_status": "created",
    "verification_status": "verified",
    "mqtt_config_pushed": true,
    "fleet_state": {"total_devices": 6, "online_devices": 6}
  }
}
```

#### Via CLI Runner

```bash
# Recommendation mode (shows plan, human_input_required=True)
python run_agents.py --onboard "Sensor-042"

# With specific firmware
python run_agents.py --onboard "Sensor-042" --onboard-firmware 2.0.0

# Auto-register mode
python run_agents.py --onboard "Sensor-042" --onboard-auto

# Full parameters
python run_agents.py --onboard "Sensor-042" --onboard-firmware 2.0.0 --onboard-ip 10.0.0.100 --onboard-mqtt-id esp32-s042 --onboard-auto
```

#### Conflict Detection

If a device with the same name or MQTT client ID already exists, the agent reports structured conflicts instead of registering a duplicate:

```json
"conflicts": [
  {
    "type": "name",
    "existing_device_id": "e5f6g7h8-...",
    "message": "Device name 'Sensor-042' is already used by device e5f6g7h8..."
  }
]
```

You can resolve the conflict by choosing a different name and re-submitting.

#### CLI Runner

```bash
# Run all agents
python run_agents.py

# OTA campaign only
python run_agents.py --ota

# V2G dispatch
python run_agents.py --v2g

# Onboard a device
python run_agents.py --onboard "Sensor-042" --onboard-auto

# JSON output for scripting
python run_agents.py --json | jq .
```

### Cleanup

```bash
docker compose down -v
```
