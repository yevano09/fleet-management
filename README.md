# Fleet Commander — IoT Device Management Module

A production-grade IoT fleet management system built with FastAPI, MQTT, Prometheus, and Grafana. Supports device registration, remote configuration, and OTA firmware updates with automatic rollback.

## Architecture

```
┌─────────────┐    MQTT     ┌─────────────┐   HTTP    ┌────────────┐
│  Device     │◄───────────►│  Mosquitto   │◄─────────►│  FastAPI   │
│  Simulators │   iot/fleet/ │  (Broker)   │  REST API │  Backend   │
│  (x5-N)     │  .../command │             │           │  :8000     │
└─────────────┘  .../status  └──────┬──────┘           └─────┬──────┘
                                    │                        │
                                    │                 ┌──────┴──────┐
                                    │                 │  SQLite/    │
                                    │                 │  Postgres   │
                                    │                 └─────────────┘
                            ┌───────┴───────┐
                            │   Prometheus   │
                            │   :9090        │
                            └───────┬───────┘
                                    │
                            ┌───────┴───────┐
                            │   Grafana     │
                            │   :3000       │
                            └───────────────┘
```

### MQTT Topic Structure

| Topic Pattern | Direction | Purpose |
|---|---|---|
| `iot/fleet/{device_id}/command/ota` | Backend → Device | OTA firmware update command (URL + SHA256) |
| `iot/fleet/{device_id}/command/config` | Backend → Device | Remote configuration push |
| `iot/fleet/{device_id}/status/ota` | Device → Backend | OTA lifecycle status updates |
| `iot/fleet/{device_id}/heartbeat` | Device → Backend | Periodic heartbeat with uptime & signal |
| `iot/fleet/register` | Device → Backend | Auto-registration on first connect |

### OTA State Machine

```
pending → downloading → applying → verifying → success
                                         → hash_mismatch → rollback → rolled_back
                              → failed (timeout / max retries)
```

On `hash_mismatch`: the backend logs the failure, the device simulator auto-reverts to the previous firmware, and the deployment is marked `rolled_back`.

## Quick Start

### Prerequisites

- Docker & Docker Compose v2

### Start the Full Environment

```bash
# Clone and enter the project
cd fleet-management

# Start all services (backend, MQTT, Prometheus, Grafana, simulator)
docker compose --profile demo up --build -d

# Wait for everything to be healthy (about 30 seconds)
docker compose ps
```

This spins up: backend (FastAPI :8000), Mosquitto (:1883), Prometheus (:9090), Grafana (:3000), and a device simulator (5 virtual devices with 20% OTA failure rate).

**Note:** The simulator and tests are behind Docker Compose profiles. Use `--profile demo` to include the simulator, and `--profile testing` to run tests. The simulator waits for the backend health check to pass before starting, ensuring MQTT subscriptions are registered before devices send messages.

### Access the Interfaces

| Service | URL | Credentials |
|---|---|---|
| Fleet Dashboard | http://localhost:8000 | — |
| API Docs (Swagger) | http://localhost:8000/docs | — |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin / admin |

## Running Tests

```bash
# Run E2E tests against the running stack
docker compose --profile testing run --build --rm tests
```

## API Reference

### Devices

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/devices/register` | Register a device (auto-registers on first connect) |
| `POST` | `/devices/{id}/heartbeat` | Update last_seen, uptime, signal strength |
| `GET` | `/devices` | List all devices with firmware, status, signal |

### OTA

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ota/upload` | Upload firmware binary (generates SHA256) |
| `POST` | `/ota/trigger` | Trigger targeted or bulk OTA update |
| `GET` | `/ota/status` | View OTA deployment statuses |
| `GET` | `/ota/firmware` | List uploaded firmware versions |

### Observability

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/metrics` | Prometheus metrics endpoint |

### Dashboard

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Fleet UI Dashboard |

### Agent Recommendations (Phase 1 — Assisted Mode)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/agents/recommendations` | Run all three agents (OTA, anomaly, groups) |
| `GET` | `/agents/ota-campaign` | Canary-based rollout plan for a firmware version |
| `GET` | `/agents/anomaly-check` | Fleet health scan: weak signals, stuck OTAs, failure spikes |
| `GET` | `/agents/device-groups` | Device groupings by firmware version and signal strength |

All agent endpoints return structured JSON with agent name, type, summary, and details. OTA and group agents mark `"human_input_required": true`.

**CLI runner:**
```bash
python run_agents.py --ota --firmware 2.0.0
python run_agents.py --anomaly
python run_agents.py --json
```

**Dashboard:** Agent recommendation panels auto-refresh every 30 seconds at the bottom of `http://localhost:8000`.

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/fleet.db` | Database connection string |
| `MQTT_BROKER_HOST` | `localhost` | MQTT broker hostname |
| `MQTT_BROKER_PORT` | `1883` | MQTT broker port |
| `OTA_TIMEOUT_SECONDS` | `120` | OTA deployment timeout |
| `MAX_RETRY_COUNT` | `3` | Max OTA retry attempts |
| `SIMULATOR_DEVICE_COUNT` | `5` | Virtual devices to simulate |
| `SIMULATOR_HEARTBEAT_INTERVAL` | `10` | Seconds between heartbeats |
| `SIMULATOR_OTA_FAILURE_RATE` | `0.2` | Probability of OTA hash mismatch |

## Security & Dependency Management

All Python dependencies are pinned to exact versions in `requirements.txt`. Dependabot is configured for automated vulnerability alerts.

### Recent Fixes (Session 2 — May 2026)

11 packages were upgraded to patch known CVEs, including critical fixes for `python-multipart` (path traversal → RCE, CVSS 8.6), `jinja2` (sandbox escape, CVSS 8.8), and `requests` (credential leak). All 3 Dockerfiles were hardened with `apt-get upgrade -y` at build time.

See [`AGENTS.md`](AGENTS.md) for the full version change table and [`SECURITY.md`](SECURITY.md) for the security policy.

```bash
# Audit dependencies for known vulnerabilities
pip install safety
safety check -r requirements.txt

# Scan Docker images
docker scout quick fleet-management-backend
```

**Cadence**: Python packages reviewed monthly. Docker base images reviewed monthly.

## Scaling for Production

For larger deployments:

1. **Database**: Switch from SQLite to PostgreSQL by setting `DATABASE_URL=postgresql+psycopg2://user:pass@postgres:5432/fleet` and using the `production` profile:
   ```bash
   docker compose --profile production up -d
   ```

2. **MQTT**: Replace the single Mosquitto node with a cluster or use a managed MQTT service.

3. **Backend**: Scale horizontally behind a reverse proxy:
   ```bash
   docker compose up -d --scale backend=3
   ```

4. **Firmware Storage**: Mount an S3-compatible bucket or NFS volume instead of the local volume.

5. **Monitoring**: Increase Prometheus retention and add alerting rules for device offline events.

## Project Structure

```
fleet-management/
├── app/                      # FastAPI application
│   ├── main.py               # App entry point, lifespan, routes
│   ├── config.py             # Pydantic settings (env-based)
│   ├── database.py           # SQLAlchemy async engine & session
│   ├── models.py             # ORM models (Device, Firmware, OtaDeployment)
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── mqtt_client.py        # MQTT client wrapper
│   ├── ota_manager.py        # OTA state machine + timeout watcher
│   ├── metrics.py            # Prometheus metrics definitions
│   ├── routers/              # API route handlers
│   │   ├── devices.py        # Device registration, heartbeat, listing
│   │   ├── ota.py            # Firmware upload, OTA trigger, status
│   │   └── dashboard.py      # Dashboard HTML serving
│   └── templates/            # Jinja2 templates
│       └── dashboard.html    # Fleet UI dashboard
├── agents/                   # Phase 1 Crew AI agents
│   ├── __init__.py            # Package init
│   ├── tools.py               # HTTP-based tools (CLI mode)
│   ├── async_tools.py         # Async DB-backed tools (in-backend mode)
│   ├── phase1_crew.py         # Crew AI agent definitions + fallbacks
│   └── routers.py             # FastAPI router (/agents/*)
├── simulator/
│   └── simulator.py          # Virtual device simulator
├── tests/
│   └── test_e2e.py           # End-to-end integration tests
├── run_agents.py             # CLI runner for Phase 1 agents
├── docker/
│   ├── prometheus/           # Prometheus scrape config
│   ├── grafana/              # Grafana provisioning + dashboards
│   └── mosquitto/            # Mosquitto MQTT broker config
├── docker-compose.yml        # Multi-service orchestration
├── Dockerfile                # Backend container image
├── Dockerfile.simulator      # Simulator container image
├── Dockerfile.tests          # Test runner container image
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable templates
├── README.md                 # This file
└── DEMO_GUIDE.md             # Presentation scripts
```
