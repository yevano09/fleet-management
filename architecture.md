# Fleet Commander — Architecture Diagram

Interactive HTML diagram at `architecture.html`. Open in any browser and click through 10 flows with animated data packets, a side panel showing real payloads, and a dev/prod mode toggle.

---

## Components

| Node | Role | Tech | Port |
|---|---|---|---|
| **User (Browser)** | Dashboard UI | Jinja2 + Chart.js + Leaflet · auto-refresh (5s/10s/15s/30s) | localhost:8181 |
| **FastAPI Backend** | Orchestrator — REST + MQTT + Agents + Schedulers | Python · FastAPI · SQLAlchemy async · 99 routes | :8000 |
| **Aegis Engine** | Auto-remediation — scrape → classify → decide → act | 8 rules · DLQ · dry-run · co-located | scrapes /metrics every 15s |
| **OTA Scheduler** | Scheduled OTA campaigns with blackout windows | Background loop · 30s interval | co-located |
| **Command Queue Flusher** | Delivers queued commands on device reconnect | Background loop · 15s interval | co-located |
| **SQLite / PostgreSQL** | Primary datastore — 16 tables | Dev: aiosqlite · fleet.db / Prod: psycopg2 | file-based / :5432 |
| **Mosquitto MQTT** | Message broker — 11 topic patterns | eclipse-mosquitto:2 · pub/sub | :1883 |
| **Device Simulator** | Virtual IoT devices (5, 20% OTA fail rate, 3 EVs) | Python · paho-mqtt · telemetry + GPS + battery | MQTT heartbeat 10s |
| **Prometheus** | Metrics collection — 30+ metrics | v2.53.0 · 7d retention | :9090 |
| **Grafana** | Visualization dashboards | 11.1.0 · pre-provisioned | :3000 |
| **Live Fleet Map** | Interactive device location + geofence overlays | Leaflet 1.9.4 · OpenStreetMap · city-color markers | dashboard embed |
| **Alert Channels** | Multi-channel notifications | Slack Webhook · SMTP Email · Generic Webhook | SMTP :587 |
| **Event Emitter** | Outbound webhook fan-out with HMAC signing | Python · requests · async delivery | co-located |

---

## Database Schema (16 Tables)

| Table | Purpose |
|---|---|
| `devices` | Device records (GPS, battery, lifecycle, city, claim_token) |
| `firmware` | Firmware binaries (SHA256 + Ed25519 signature) |
| `ota_deployments` | OTA deployment tracking (state machine) |
| `ota_schedules` | Scheduled OTA campaigns (Feature 4) |
| `v2g_schedules` | V2G charge/discharge schedules |
| `alerts` | Alert records (dedup, escalation, lifecycle) |
| `user_sessions` | OAuth + admin session tracking (RBAC roles) |
| `telemetry` | Time-series telemetry per heartbeat (Feature 1) |
| `geofences` | Geofence definitions (circle/polygon) (Feature 2) |
| `geofence_events` | Geofence enter/exit events (Feature 2) |
| `command_queue` | Offline command buffer (Feature 5) |
| `audit_logs` | Audit trail for all mutating actions (Feature 6) |
| `device_shadows` | Desired/reported shadow states (Feature 7) |
| `predicted_failures` | Predictive maintenance predictions (Feature 3) |
| `webhook_subscriptions` | Outbound webhook configs (Feature 11) |
| `event_log` | Emitted event delivery tracking (Feature 11) |
| `remediations` | Aegis remediation records |
| `rule_configs` | Aegis rule override configs |

---

## Flows (step-by-step)

### 1. Device Registration & Heartbeat

A device connects to the fleet for the first time or reconnects after being offline.

1. **Simulator → MQTT** — Publishes `iot/fleet/register` with device_id, name, firmware_version, ip_address, city
2. **MQTT → Backend** — `handle_mqtt_register()` upserts the device; if reconnect, flushes queued commands + syncs shadow
3. **Backend → DB** — INSERT (new device) or UPDATE (re-registration); audit log + event emitted
4. **Simulator → MQTT** — Publishes heartbeat every 10s with uptime, signal, EV battery, GPS, CPU/memory/temp telemetry
5. **MQTT → Backend** — `handle_mqtt_heartbeat()` updates device; records telemetry point; checks geofences
6. **Backend → DB** — Device state freshened; 60+ seconds without heartbeat shows device as offline

```mermaid
sequenceDiagram
    participant Sim as Device Simulator
    participant MQ as Mosquitto MQTT
    participant BE as FastAPI Backend
    participant DB as Database

    Sim->>MQ: Publish iot/fleet/register (with city)
    MQ->>BE: handle_mqtt_register()
    BE->>DB: INSERT / UPDATE device + audit log
    DB-->>BE: Device record
    BE->>BE: Flush queued commands (if reconnect)
    BE->>BE: Sync desired shadow (if reconnect)
    loop every 10s
        Sim->>MQ: Heartbeat (telemetry + GPS + battery)
        MQ->>BE: handle_mqtt_heartbeat()
        BE->>DB: Update device + record Telemetry point
        BE->>BE: Check geofences (if GPS)
    end
    Note over DB: 60s no heartbeat → offline
```

### 2. OTA Firmware Update with Signing & Rollback

Full lifecycle from firmware upload (with optional Ed25519 signing) through deployment with automatic rollback.

1. **User → Backend** — POST `/ota/upload` with firmware binary; SHA256 + optional Ed25519 signature computed
2. **Backend → DB** — Firmware record persisted with signature fields; audit log written
3. **User → Backend** — POST `/ota/trigger`; OtaDeployment records created; timeout watcher started
4. **Backend → MQTT** — Publishes OTA command with firmware_url, sha256_hash, deployment_id
5. **Simulator → MQTT** — Reports status: downloading → applying → verifying → success or hash_mismatch → rollback
6. **Backend → DB** — Updates deployment + device firmware version (or restores previous on rollback)
7. **Event emitted** — `ota.triggered` event fires to webhook subscribers

### 3. Scheduled OTA with Maintenance Windows

1. **User → Backend** — POST `/ota/schedules` with firmware, target time, blackout hours, canary %
2. **Backend → DB** — Schedule record created with status `scheduled`
3. **Scheduler loop (30s)** — Checks for due schedules; skips if within blackout window
4. **Backend → MQTT** — Publishes OTA commands (canary first, then rest)
5. **Backend → DB** — Schedule marked `completed` with deployment IDs; event emitted

### 4. Telemetry Time-Series & Predictive Maintenance

1. **Device → MQTT** — Heartbeat includes cpu_usage, memory_usage, temperature, soc, soh, battery_temp
2. **Backend → DB** — Telemetry point recorded for every heartbeat
3. **User → Backend** — POST `/predictive/scan` triggers analysis
4. **Backend → DB** — Linear-regression slope analysis on signal, temp, SOH, uptime trends
5. **Backend → DB** — PredictedFailure records created for devices with risk > 0.4
6. **Dashboard** — Device detail modal shows Chart.js trend charts; predictive panel shows risk meters

### 5. Geofencing & Geo-alerts

1. **User → Backend** — POST `/geofences` creates a circle or polygon geofence
2. **Device → MQTT** — Heartbeat with GPS coordinates
3. **Backend → DB** — `check_device_position()` compares position against all enabled geofences
4. **Backend → DB** — GeofenceEvent created on enter/exit transition
5. **AlertEngine** — Geofence events converted to anomalies → alerts (dedup, notify)
6. **Dashboard** — Geofence circles drawn on Leaflet map; events list in geofence panel

### 6. Offline Command Queue & Device Shadow

1. **User → Backend** — POST `/commands/queue` for an offline device → status `queued`
2. **Device reconnects** — `handle_mqtt_register()` triggers `_flush_command_queue()`
3. **Backend → MQTT** — Queued commands published; status → `delivered`
4. **Shadow sync** — `_sync_shadow_to_device()` pushes latest desired state on reconnect
5. **Device → MQTT** — V2G status reports create `reported` shadow entries

### 7. Fleet Dashboard

The live monitoring UI with auto-refreshing panels, Chart.js charts, Leaflet map, and modals.

1. **User → Backend** — GET `/` with auth check (Google OAuth or admin)
2. **Backend → User** — Rendered Jinja2 HTML with Chart.js, Leaflet, auto-refresh JS
3. **Dashboard polls** — Devices (5s), MQTT status (10s), alerts (10s), Aegis (10s), agents (30s), predictions (30s), geofences (60s), schedules (30s), queue (15s)
4. **Device detail modal** — Tabs: Telemetry (Chart.js charts), Shadow, Lifecycle, Commands
5. **Prometheus → Grafana** — 30+ metrics scraped every 10s; pre-provisioned dashboards

### 8. Alert Pipeline + Aegis Integration

1. **User → Backend** — GET `/agents/fleet-health` triggers anomaly detection
2. **Backend → DB** — Checks for 8 anomaly types (weak_signal, stuck_ota, ota_failure_spike, mass_offline, device_offline, v2g_revenue_drop, geofence_enter, geofence_exit)
3. **AlertEngine** — Dedup (type + device_id), cooldown (120-3600s), escalation (3× → critical)
4. **Channels** — Fans out to Slack, Email, Webhook
5. **Aegis** — Critical anomalies may trigger auto-remediation (8 rules, DLQ, retry)
6. **Dashboard** — Alert panel with acknowledge/resolve buttons; badge count in header

### 9. Aegis Auto-Remediation

1. **Aegis → Backend** — Scrape loop (15s) polls `/metrics`, parses fleet_* signals
2. **Aegis → Aegis** — Classifies signals; matches against 8 priority-ordered rules (with cooldown)
3. **Aegis → MQTT** — Executes remediation actions (throttle_ota, device_restart, qos_downgrade, etc.)
4. **Aegis → DB** — Records Remediation with input/output snapshots, duration, status
5. **Aegis → Prometheus** — 7 aegis_* metrics updated
6. **Dashboard** — 3-column panel: signals / active / history (auto-refresh 10s)

### 10. Device Lifecycle & Provisioning

1. **Pre-register** — POST `/provisioning/pre-register` creates offline device with claim_token
2. **Bulk import** — POST `/provisioning/bulk-import` (CSV) creates multiple devices with tokens
3. **Claim** — Device claims itself via POST `/lifecycle/claim` with token
4. **Maintenance** — POST `/lifecycle/{id}/maintenance` → MQTT maintenance command
5. **Decommission** — POST `/lifecycle/{id}/decommission` → lifecycle_status = decommissioned
6. **Audit** — Every lifecycle transition logged + Prometheus metric incremented

---

## Modes

| Aspect | Dev (SQLite) | Prod (PostgreSQL) |
|---|---|---|
| Database | SQLite via aiosqlite | PostgreSQL via psycopg2 |
| Connection | file-based (fleet.db) | TCP :5432 |
| Setup | Default — no extra config | Requires `--profile production` |
| Alert Channels | Slack only | Slack + Email + Webhook |

---

## Workshop Scenarios

1. **"Show me how a device joins the fleet"** — Flow 1: MQTT registration → DB persist → heartbeat → telemetry → geofence check
2. **"What happens when an OTA fails?"** — Flow 2: 20% failure rate → hash_mismatch → automatic rollback
3. **"Can I schedule OTA for off-peak hours?"** — Flow 3: Scheduled OTA with blackout windows + canary
4. **"Can the system predict failures?"** — Flow 4: Telemetry trends → predictive maintenance → risk scores
5. **"How do geofences work?"** — Flow 5: GPS heartbeat → geofence check → enter/exit alerts → map overlay
6. **"What happens when a device is offline?"** — Flow 6: Command queue → reconnect flush → shadow sync
7. **"How does the dashboard stay live?"** — Flow 7: 9 auto-refresh intervals + Chart.js + Leaflet + modals
8. **"How do alerts get to Slack?"** — Flow 8: Anomaly detection → dedup → escalation → multi-channel
9. **"How does Aegis auto-heal the fleet?"** — Flow 9: scrape → classify → decide → act → record → 8 rules
10. **"How do I provision devices at scale?"** — Flow 10: Bulk CSV import → QR-claim → lifecycle management
