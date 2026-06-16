# Fleet Commander — Architecture Diagram

Interactive HTML diagram at `architecture.html`. Open in any browser and click through 5 flows with animated data packets, a side panel showing real payloads, and a dev/prod mode toggle.

---

## Components

| Node | Role | Tech | Port |
|---|---|---|---|
| **User (Browser)** | Dashboard UI | Jinja2 + HTMX · auto-refresh (5s/10s/30s) | localhost:8000 |
| **FastAPI Backend** | Orchestrator — REST + MQTT + Agents | Python · FastAPI · SQLAlchemy async | :8000 |
| **SQLite / PostgreSQL** | Primary datastore | Dev: aiosqlite · fleet.db / Prod: psycopg2 | file-based / :5432 |
| **Mosquitto MQTT** | Message broker | eclipse-mosquitto:2 · pub/sub | :1883 |
| **Device Simulator** | Virtual IoT devices (5, 20% OTA fail rate) | Python · paho-mqtt · async | MQTT heartbeat 10s |
| **Prometheus** | Metrics collection | v2.53.0 · 7d retention | :9090 |
| **Grafana** | Visualization dashboards | 11.1.0 · pre-provisioned | :3000 |
| **Alert Channels** | Multi-channel notifications | Slack Webhook · SMTP Email · Generic Webhook | SMTP :587 |

---

## Flows (step-by-step)

### 1. Device Registration & Heartbeat

A device connects to the fleet for the first time or reconnects after being offline.

1. **Simulator → MQTT** — Publishes `iot/fleet/register` with device_id, name, firmware_version, ip_address
2. **MQTT → Backend** — `handle_mqtt_register()` upserts the device (match by mqtt_client_id, id, or name)
3. **Backend → DB** — INSERT (new device) or UPDATE (re-registration); Prometheus counters increment
4. **Simulator → MQTT** — Publishes heartbeat every 10s to `iot/fleet/{id}/heartbeat` with uptime, signal, EV battery telemetry
5. **MQTT → Backend** — `handle_mqtt_heartbeat()` updates last_seen, tracks V2G metrics via Prometheus
6. **Backend → DB** — Device state freshened; 60+ seconds without heartbeat shows device as offline (transient)

### 2. OTA Firmware Update

Full lifecycle from firmware upload through device deployment with automatic rollback on hash mismatch.

1. **User → Backend** — POST `/ota/upload` with firmware binary; SHA256 hash computed, file stored
2. **Backend → DB** — Firmware record persisted; duplicate version numbers rejected (HTTP 409)
3. **User → Backend** — POST `/ota/trigger` targeting all devices or specific IDs; OtaDeployment records created
4. **Backend → MQTT** — Publishes OTA command to `iot/fleet/{id}/command/ota` with firmware_url, sha256_hash, deployment_id
5. **MQTT → Simulator** — Device subscribes and simulates downloading → applying → verifying
6. **Simulator → MQTT** — Publishes status to `iot/fleet/{id}/status/ota`: success or hash_mismatch → rollback → rolled_back
7. **MQTT → Backend** — `OtaStateMachine.handle_ota_status()` validates state transitions
8. **Backend → DB** — Updates OtaDeployment.status and Device.firmware_version (or restores previous on rollback)

State machine: `pending → downloading → applying → verifying → success` or `→ hash_mismatch → rollback → rolled_back`

### 3. Fleet Dashboard

The live monitoring UI with auto-refreshing device table, agent panels, and alert badge.

1. **User → Backend** — GET `/` with auth check (Google OAuth or admin basic auth)
2. **Backend → DB** — Queries all devices with status, firmware, signal, battery data
3. **Backend → User** — Returns rendered Jinja2 HTML with HTMX auto-refresh directives
4. **Backend → Prometheus** — `/metrics` endpoint scraped every 15s (fleet size, OTA, latency, MQTT, alerts, V2G)
5. **Prometheus → Grafana** — Pre-provisioned dashboards query PromQL for visualizations

### 4. Agent Recommendations

Three Phase 1 AI agents analyze fleet state and return structured recommendations.

1. **User → Backend** — GET `/agents/recommendations` triggers all 3 agents concurrently
2. **Backend → DB** — OTA agent queries firmware + devices; anomaly agent checks signals/heartbeats/OTAs; group agent clusters by firmware and signal
3. **DB → Backend** — Raw fleet data returned for heuristic processing
4. **Backend → User** — Structured JSON with OTA campaign plan, anomaly report, and device groups (human_input_required flagged)

### 5. Alert Pipeline

Anomaly detection → AlertEngine dedup/cooldown → multi-channel notifications.

1. **User → Backend** — GET `/agents/fleet-health` triggers anomaly detection with mandatory alerting
2. **Backend → DB** — Heuristic checks for device_offline, weak_signal, stuck_ota, v2g_revenue_drop
3. **Backend → AlertEngine** — Processes anomalies through dedup (type + device_id), cooldown (300-3600s), and escalation (3× count → critical)
4. **AlertEngine → Channels** — Fans out to Slack (rich attachment), Email (SMTP), Webhook (JSON POST)
5. **Backend → DB** — Alert records persisted with active/acknowledged/resolved lifecycle
6. **Backend → User** — Returns processed alert status; dashboard panel refreshes every 10s

---

## Modes

| Aspect | Dev (SQLite) | Prod (PostgreSQL) |
|---|---|---|
| Database | SQLite via aiosqlite | PostgreSQL via psycopg2 |
| Connection | file-based (fleet.db) | TCP :5432 |
| Setup | Default — no extra config | Requires `--profile production` |
| Alert Channels | Slack only | Slack + Email + Webhook |

Toggle with the Dev/Prod button or press `O`.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Play / Pause auto-advance |
| `←` / `→` | Previous / Next step |
| `1`–`5` | Select flow tab |
| `O` | Toggle dev/prod mode |
| `T` | Toggle dark/light theme |
| `F` | Fullscreen canvas |
| `R` | Reset node positions |
| Drag | Reposition any node |

---

## Workshop Scenarios

1. **"Show me how a device joins the fleet"** — Click flow 1 and watch the MQTT registration → DB persist → heartbeat loop
2. **"What happens when an OTA fails?"** — Flow 2 step 6: 20% failure rate triggers hash_mismatch → automatic rollback
3. **"How does the dashboard stay live?"** — Flow 3: HTMX auto-refresh + Prometheus scraping + Grafana dashboards
4. **"What can the AI agents tell me?"** — Flow 4: all 3 agents run concurrently with heuristic logic (no LLM API key needed)
5. **"How do alerts get to Slack?"** — Flow 5: anomaly detection → dedup → escalation → Slack/Email/Webhook
