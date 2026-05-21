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

**Note:** The simulator and tests are behind Docker Compose profiles. Use `--profile demo` to include the simulator, and `--profile testing` to run tests.

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
├── simulator/
│   └── simulator.py          # Virtual device simulator
├── tests/
│   └── test_e2e.py           # End-to-end integration tests
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
