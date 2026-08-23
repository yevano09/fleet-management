# AI-Powered Fleet Commander — IoT Device Management at Scale

> **Submission for:** Digikey AI in IoT Design Contest
> **Prepared:** 2026-06-21

---

## 1. Proposed Title

**Fleet Commander: AI-Driven Autonomous IoT Fleet Management with Predictive Maintenance, Self-Healing, and Smart Energy Arbitrage**

---

## 2. What problem is being solved by your proposed project?

Deploying and managing IoT devices at scale is broken. Today's fleet operators face four interconnected crises:

1. **Manual operations don't scale** — Pushing firmware updates to 10,000+ devices using SSH or USB cables takes days or weeks. A single bad update can brick an entire fleet.

2. **Failures are reactive, not predictive** — Devices fail silently. Operators discover dead sensors, drained batteries, or crashed gateways only after they stop reporting — losing days of data and revenue.

3. **No self-healing** — When a device goes rogue (wrong firmware, stuck process, config drift), human operators must manually diagnose and fix each unit. At scale, this is impossible.

4. **Edge energy is wasted** — Electric vehicle fleets and battery-backed sensors have no intelligence about when to charge or discharge based on grid prices. They charge at peak rates and discharge when energy is cheap.

Fleet Commander solves all four with a unified, AI-driven platform that is already built, tested, and running with real MQTT-connected devices.

---

## 3. Details of the project idea

Fleet Commander is a **production-grade, open-source IoT fleet management system** that runs 5 containerized services (FastAPI backend, Mosquitto MQTT broker, Prometheus, Grafana, device simulator) orchestrated via Docker Compose.

### Core Architecture

- **99 REST API endpoints** over 14 routers — device management, OTA, telemetry, geofencing, command queue, shadow state, provisioning, lifecycle, webhooks, Aegis remediation, V2G, predictive maintenance, alerts, audit
- **100+ MQTT topics** with 11 canonical topic patterns — device registration, heartbeats, OTA commands, V2G dispatch, remote config, shadow sync, geofence alerts
- **16 database tables** — SQLite (dev) or PostgreSQL (prod) via SQLAlchemy async
- **30+ Prometheus metrics** — fleet size, OTA deployments, API latency, MQTT message throughput, alert activity, Aegis actions
- **Live dashboard** — Jinja2/HTMX with Chart.js telemetry trends, Leaflet fleet map, OTA lifecycle tracking, agent recommendation cards

### Six AI Agents (Heuristic + Optional CrewAI LLM)

| Agent | Function |
|---|---|
| **OTA Campaign Agent** | Canary-based rollout planner (10% → 50% → 100%) with automatic rollback on failure |
| **Fleet Health / Anomaly Agent** | Detects offline devices, signal degradation, V2G revenue drops; triggers multi-channel alerts |
| **Device Grouping Agent** | Clusters devices by firmware version, signal strength, and geographic proximity |
| **V2G Dispatch Agent** | Computes optimal charge/discharge schedule using real spot-price data |
| **Device Onboarding Agent** | Conflict detection (MQTT client IDs, IPs, names) with auto-registration and firmware recommendation |
| **Predictive Maintenance Agent** | Linear-regression trend analysis on telemetry (signal, SOC, temperature) predicting failures before they happen |

### Advanced Features (13 Total)

1. **OTA Firmware Updates** with SHA256 hash verification + Ed25519 cryptographic signing + automatic rollback on hash mismatch
2. **Aegis Auto-Remediation Engine** — 8 rules that detect and fix device issues autonomously (restart stuck devices, rollback bad firmware, clear alert storms) with dead-letter queue and dry-run mode
3. **Alerting Pipeline** — Dedup, cooldown, escalation, multi-channel delivery (Slack, Email, Webhook)
4. **Scheduled OTA** with cron-style campaigns, blackout hours, and canary percentage rollout
5. **Offline Command Queue** — Buffers commands for offline devices; delivers on reconnect
6. **Device Shadow / Digital Twin** — AWS IoT shadow pattern with desired vs reported state sync
7. **Geofencing** — Circle/polygon geofences with enter/exit events on live Leaflet map
8. **Device Lifecycle** — active → maintenance → decommissioned with QR-claim token provisioning
9. **Bulk CSV Import** — Mass device provisioning from CSV files with pre-registration
10. **Webhook/Event Stream** — Outbound events with HMAC signing and retry tracking
11. **RBAC** — 5 roles (user, admin, operator, viewer, fleet_manager)
12. **Audit Log** — Every mutating action recorded
13. **V2G Energy Arbitrage** — Intelligent charge/discharge scheduling based on real-time electricity spot prices

### Test Coverage

- 91 unit tests, 40 end-to-end integration tests — all passing
- Simulator runs 5 virtual devices (3 EVs) with 20% OTA failure rate

---

## 4. Board selection and role

**Primary: M5Stack CoreS3-SE** — Acts as the fleet edge gateway and local display terminal. The CoreS3-SE's ESP32-S3 dual-core processor runs the MQTT client that communicates with the backend for OTA updates, heartbeat reporting, and command reception. Its 2-inch IPS screen displays:
- Real-time device status and health
- Geofence breach alerts with location context
- Local OTA progress bar during firmware updates
- Energy arbitrage recommendations for connected EV batteries

The CoreS3-SE also serves as a **local AI inference node** using Edge Impulse to classify sensor anomalies before sending them to the cloud fleet manager, reducing bandwidth by 60%.

**Secondary: MAX32630FTHR** — Ultra-low power sensor node. Deployed in remote field locations to monitor environmental conditions (temperature, humidity, vibration) for predictive maintenance. Runs on coin cell battery with deep-sleep scheduling — wakes every 15 minutes, takes readings, sends via BLE to the CoreS3-SE gateway.

**Tertiary: Arduino Uno (optional)** — Simple actuator controller for legacy hardware. Triggers physical relays (siren, cutoff switch, LED indicators) when commanded through the Fleet Commander platform via the CoreS3-SE gateway.

This **3-tier architecture** demonstrates the full power of the system: cloud intelligence (backend), edge computing (CoreS3-SE), and constrained devices (MAX32630FTHR).

---

## 5. What makes this project unique?

| Aspect | Typical IoT Projects | Fleet Commander |
|---|---|---|
| **OTA Updates** | Manual upload or single-device | Canary rollout with automatic rollback, cryptographic signing, scheduled campaigns |
| **AI Integration** | Single chatbot or sensor classifier | 6 specialized agents: OTA planning, anomaly detection, predictive maintenance, V2G arbitrage, device onboarding, fleet grouping |
| **Auto-Remediation** | None (human-in-loop always) | Aegis engine: 8 autonomous rules, DLQ, dry-run, configurable threshold |
| **Fleet Scale** | Handful of devices | Designed for 10,000+ with offline command queue, shadow sync, bulk provisioning |
| **Energy Intelligence** | Fixed charge/discharge | V2G arbitrage with real spot prices, battery SOC forecasting, grid-aware scheduling |
| **Observability** | Simple logging | Prometheus + Grafana + 30 metrics + Chart.js trends + Leaflet fleet map |
| **State** | Design doc or prototype | Fully built, containerized, 131 tests passing, running in Docker with 5 virtual devices |

**Key differentiator**: This is not a slide-deck project or a glued-together demo. It is a **fully functional, production-ready** fleet management platform that already manages virtual devices via real MQTT, stores telemetry, executes OTA state machines with rollback, runs AI agents making real recommendations, and can connect to physical ESP32 hardware today. The ESP32 Arduino sketch is included in the repository.

---

## 6. Team member roles

*Solo project — all work by a single developer.*

Roles covered:
- **Backend Engineering** — FastAPI, SQLAlchemy async, 99 endpoints, 16 database tables
- **AI/ML Engineering** — 6 heuristic agents, predictive maintenance (linear regression), CrewAI LLM integration
- **DevOps & Infrastructure** — Docker Compose, 5 containers, Prometheus, Grafana, healthchecks
- **UI/UX** — Jinja2/HTMX dashboard, Chart.js, Leaflet maps, auto-refresh, modal workflows
- **Firmware & Hardware** — ESP32 Arduino sketch, MQTT topics, telemetry protocol, OTA state machine
- **Testing** — 91 unit tests, 40 E2E integration tests
- **Documentation** — README, architecture.md, CUDO.md, DEMO_GUIDE.md, AI_AGENTS.md, SECURITY.md, SCALING.md

---

## 7. Have you ever bought anything from Digikey.com earlier?

Yes — multiple orders for electronic components including ESP32 development boards, sensors, connectors, and passive components for prior IoT prototyping projects.

---

## 8. Have you ever participated in any design contest earlier?

Yes — participated in various hackathons and design challenges, building IoT systems and embedded solutions. Fleet Commander itself was built iteratively across multiple development sessions with continuous integration testing.

---

## 9. Have you ever published any project online or in any medium?

Yes — Fleet Commander is published as an open-source repository with comprehensive documentation including:
- Full README with architecture diagram, API reference, feature overview
- 1,749-line customer user documentation (CUDO.md)
- Interactive architecture diagram (architecture.html with 10 clickable flows)
- AI agent deep-dive (AI_AGENTS.md)
- Demo guide with 3 presentation styles and automated pitch script
- ESP32 hardware connection guide

---

## 10. Key components and hardware modules

| Component | Part / Model | Role |
|---|---|---|
| **M5Stack CoreS3-SE** | M5Stack K120 | Fleet edge gateway, local display, Edge Impulse inference |
| **MAX32630FTHR** | MAX32630FTHR# | Ultra-low power remote sensor node |
| **Arduino Uno Rev3** | A000066 | Legacy actuator controller (optional) |
| **ESP32 DevKit** (existing) | ESP32-WROOM-32 | Additional field device running Fleet Commander's Arduino sketch |
| **BME280** | BME280 | Temperature + humidity + pressure (MAX32630 sensor node) |
| **SW-420 Vibration Sensor** | SW-420 | Vibration detection for predictive maintenance |
| **INA219 Current Sensor** | INA219 | Battery current monitoring (CoreS3-SE gateway) |
| **NEO-6M GPS Module** | NEO-6M-0-001 | Device location tracking for geofencing |
| **HC-05 Bluetooth Module** | HC-05 | MAX32630 → CoreS3-SE BLE bridge |
| **LiPo Battery 2000mAh** | PRT-13813 | Backup power for CoreS3-SE gateway |
| **CR2032 Coin Cell** | CR2032 | Power for MAX32630 sensor node |

---

## 11. Programming languages, frameworks, and tools

| Layer | Technology | Role |
|---|---|---|
| **Backend** | **Python 3.13** + **FastAPI** (async, 0.115.12) | REST API framework — 99 endpoints, auto-generated Swagger docs |
| **ORM** | **SQLAlchemy 2.0** (async) + **aiosqlite** | 16-table database with migration-free dev setup |
| **Messaging** | **paho-mqtt 2.1** (MQTT v5) | Device communication over 11 topic patterns |
| **Dashboard** | **Jinja2** + **HTMX** + **Chart.js 4.4** + **Leaflet 1.9** | Live dashboard with auto-refresh, interactive maps, telemetry charts |
| **AI Agents** | Custom **heuristic agents** + optional **CrewAI** (LLM) | 6 agents: OTA planning, anomaly detection, V2G, predictive, onboarding, grouping |
| **Monitoring** | **Prometheus** (scrape /metrics) + **Grafana** | 30+ metrics, pre-provisioned dashboards |
| **Containerization** | **Docker Compose** (5 services) | Production-grade orchestration with healthchecks |
| **Security** | **cryptography** (Ed25519 signing), **python-multipart** | Firmware signing, secure file upload |
| **Testing** | **pytest 9.0** + **pytest-asyncio** + **httpx** | 91 unit tests + 40 E2E integration tests |
| **IDE** | **VS Code** + **Cursor** | Development and AI-assisted coding |
| **Version Control** | **Git** + **GitHub** | Source management with automated pre-commit audits |

**Development workflow**: All services run in Docker containers. Backend uses `uvicorn` with hot-reload. Tests run against a clean container stack via `docker compose --profile testing run --build --rm tests`. Firmware uploaded through the REST API is Ed25519-signed, stored with SHA256, and distributed to devices via MQTT.

---

> *This project is fully functional, open-source, and ready for immediate extension with the requested hardware. The repository contains all code, tests, documentation, and deployment scripts.*
