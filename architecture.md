# Fleet Commander — Architecture Diagram

Interactive HTML diagram at `architecture.html`. Open in any browser and click through 6 flows with animated data packets, a side panel showing real payloads, and a dev/prod mode toggle.

---

## Components

| Node | Role | Tech | Port |
|---|---|---|---|
| **User (Browser)** | Dashboard UI | Jinja2 + HTMX · auto-refresh (5s/10s/30s) | localhost:8181 |
| **FastAPI Backend** | Orchestrator — REST + MQTT + Agents | Python · FastAPI · SQLAlchemy async | :8000 |
| **Aegis Engine** | Auto-remediation — scrape → classify → decide → act | Python stdlib · co-located | scrapes /metrics every 15s |
| **SQLite / PostgreSQL** | Primary datastore | Dev: aiosqlite · fleet.db / Prod: psycopg2 | file-based / :5432 |
| **Mosquitto MQTT** | Message broker | eclipse-mosquitto:2 · pub/sub | :1883 |
| **Device Simulator** | Virtual IoT devices (5, 20% OTA fail rate) | Python · paho-mqtt · async | MQTT heartbeat 10s |
| **Prometheus** | Metrics collection | v2.53.0 · 7d retention | :9090 |
| **Grafana** | Visualization dashboards | 11.1.0 · pre-provisioned | :3000 |
| **Live Fleet Map** | Interactive device location visualization | Leaflet 1.9.4 · OpenStreetMap tiles · city-color markers | dashboard embed |
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

The live monitoring UI with auto-refreshing device table, agent panels, alert badge, and Aegis panel.

1. **User → Backend** — GET `/` with auth check (Google OAuth or admin basic auth)
2. **Backend → DB** — Queries all devices with status, firmware, signal, battery data
3. **Backend → User** — Returns rendered Jinja2 HTML with HTMX auto-refresh directives
4. **Backend → Prometheus** — `/metrics` endpoint scraped every 15s (fleet size, OTA, latency, MQTT, alerts, V2G, Aegis)
5. **Prometheus → Grafana** — Pre-provisioned dashboards query PromQL for visualizations including Aegis panels

### 4. Agent Recommendations

Four Phase 1 AI agents analyze fleet state and return structured recommendations.

1. **User → Backend** — GET `/agents/recommendations` triggers all 4 agents concurrently (OTA, Anomaly, Groups, Device Onboarding)
2. **Backend → DB** — OTA agent queries firmware + devices; anomaly agent checks signals/heartbeats/OTAs; group agent clusters by firmware and signal; onboarding agent checks for ID conflicts
3. **DB → Backend** — Raw fleet data returned for heuristic processing
4. **Backend → User** — Structured JSON with OTA campaign plan, anomaly report, device groups, and onboarding recommendation

### 5. Alert Pipeline + Aegis Integration

Anomaly detection → AlertEngine dedup/cooldown → Aegis auto-remediation → multi-channel notifications.

1. **User → Backend** — GET `/agents/fleet-health` triggers anomaly detection with mandatory alerting
2. **Backend → DB** — Heuristic checks for device_offline, weak_signal, stuck_ota, v2g_revenue_drop, aegis_escalation
3. **Backend → Aegis** — Critical anomalies forwarded to Aegis engine for auto-remediation (8 rule chain)
4. **Backend → AlertEngine** — Processes anomalies through dedup (type + device_id), cooldown (300-3600s), and escalation (3× count → critical)
5. **AlertEngine → Channels** — Fans out to Slack (rich attachment), Email (SMTP), Webhook (JSON POST)
6. **Backend → DB** — Alert records persisted with active/acknowledged/resolved lifecycle; Aegis results also recorded
7. **Backend → User** — Returns processed alert status + Aegis remediation result; dashboard panel refreshes every 10s

### 6. Aegis Auto-Remediation

Closing the loop between metric signals and fleet healing — scrape, classify, decide, act, record, observe.

1. **Aegis → Backend** — Scrape loop (every 15s) polls `/metrics/`, parses fleet_* signals into RemediationSignal objects
2. **Aegis → Backend** — Classifies signals by severity (INFO/WARNING/CRITICAL), matches against 8 priority-ordered rules
3. **Aegis → Aegis** — Decision engine evaluates rules in priority order; first match wins; cooldown enforcement per rule
4. **Aegis → Aegis** — Action execution with 30s timeout, exponential backoff retry (×3), rollback, dead-letter queue
5. **Aegis → MQTT** — Publishes remediation commands (throttle_ota, device_restart, qos_downgrade, etc.)
6. **Aegis → Backend** — Records immutable Remediation record (full input/output snapshots, duration, error trace)
7. **Aegis → Prometheus** — Increments 7 aegis_* Prometheus metrics (signals, decisions, remediations, duration, DLQ)
8. **Aegis → User** — Dashboard panel (3-column: signals/active/history, auto-refresh 10s, expandable entries)

Built-in rules: R001 throttle_ota, R002 mqtt_qos_downgrade, R003 device_soft_restart, R004 scale_heartbeat, R005 rollback_ota_batch, R006 human_escalation, R007 migrate_device_pool, R008 cleanup_firmware_artifacts.

### 7. GPS Fleet Tracking

Live device location tracking piggybacked on existing heartbeat flow — no new MQTT topics or endpoints.

1. **Simulator → MQTT** — After `GPS_INTERVAL` seconds, device activates GPS (firmware → `2.0.0-gps`) and includes `latitude` and `longitude` in every heartbeat to `iot/fleet/{id}/heartbeat`
2. **MQTT → Backend** — `handle_mqtt_heartbeat()` at `app/main.py:98` extracts `latitude`/`longitude` from JSON payload and sets `device.latitude`/`device.longitude`
3. **Backend → DB** — Coordinates persisted to `devices.latitude` and `devices.longitude` (nullable Float columns)
4. **Backend → User** — `GET /devices` returns lat/lng per device; dashboard renders Leaflet map from `/api/devices` HTMX data
5. **Dashboard → Leaflet** — `updateMapMarkers()` renders `L.circleMarker` per device, color-coded by city, with animated position updates and click popups; city filter buttons toggle visibility

Backend auto-creates a `2.0.0-gps` firmware record on startup (`app/main.py:126`) as a demo firmware binary. Simulator devices self-activate GPS after a timer rather than via OTA command.

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
| `1`–`7` | Select flow tab |
| `O` | Toggle dev/prod mode |
| `T` | Toggle dark/light theme |
| `F` | Fullscreen canvas |
| `R` | Reset node positions |
| Drag | Reposition any node |

---

## Workshop Scenarios

1. **"Show me how a device joins the fleet"** — Click flow 1 and watch the MQTT registration → DB persist → heartbeat loop
2. **"What happens when an OTA fails?"** — Flow 2 step 6: 20% failure rate triggers hash_mismatch → automatic rollback
3. **"How does the dashboard stay live?"** — Flow 3: HTMX auto-refresh + Prometheus scraping + Grafana dashboards (+Aegis panel)
4. **"What can the AI agents tell me?"** — Flow 4: all 4 agents run concurrently with heuristic logic (no LLM API key needed)
5. **"How do alerts get to Slack and trigger auto-remediation?"** — Flow 5: anomaly detection → Aegis rules → dedup → escalation → Slack/Email/Webhook
6. **"How does Aegis auto-heal the fleet?"** — Flow 6: scrape → classify → decide → act → record → observe — 8 rules, dead-letter queue, full audit trail
7. **"How do I see where my devices are?"** — Flow 7: GPS piggybacks on heartbeats → DB persist → Leaflet map with city-color-coded markers, popups, and city filters
