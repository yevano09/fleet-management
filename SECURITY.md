# Fleet Commander — Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅ Active development |
| < 1.0   | ❌ Pre-release |

---

## Reporting a Vulnerability

We take security seriously. If you discover a vulnerability in Fleet Commander, please follow the coordinated disclosure process below.

**Do not open public GitHub issues for security vulnerabilities.**

### Contact

- **Email:** security@fleet-commander.io
- **PGP Key:** Available at https://fleet-commander.io/pgp-key.asc
- **Response SLA:** 48 hours for initial acknowledgment

### Disclosure Process

1. **Report** — Send details to security@fleet-commander.io. Include:
   - Component and version affected
   - Type of vulnerability (XSS, RCE, auth bypass, etc.)
   - Steps to reproduce (PoC preferred)
   - Impact assessment

2. **Acknowledgment** — We confirm receipt within 48 hours and assign a tracking ID.

3. **Assessment** — Our security team triages the report within 5 business days:
   - Critical / High: Immediate fix, patch release within 48 hours
   - Medium: Fix in next sprint cycle (≤ 14 days)
   - Low: Fix in upcoming release (≤ 30 days)

4. **Fix & Release** — A patched version is published. The vulnerability is disclosed publicly after the fix ships.

5. **Credit** — Reporters are credited in release notes (unless anonymity is requested).

---

## Threat Model

### Assets Protected

| Asset | Sensitivity | Impact if Compromised |
|-------|-------------|-----------------------|
| Device credentials & identity | High | Device impersonation, fleet takeover |
| Firmware binaries | Critical | Malware injection into IoT fleet |
| OTA deployment tokens | High | Unauthorized firmware pushes |
| Device telemetry (heartbeats) | Low | Fleet topology disclosure |
| Database (device records) | High | PII exposure, configuration leak |
| MQTT broker access | Critical | Full command & control compromise |

### Threat Actors

| Actor | Capability | Motivation |
|-------|-----------|------------|
| Malicious IoT device | Low — constrained HW | Join fleet, extract firmware |
| Network attacker | Medium — MITM | Intercept OTA commands |
| External hacker | High — zero-days | Ransomware, botnet recruitment |
| Insider | Medium — credentialed | Sabotage, data exfiltration |

### Attack Surface

| Surface | Risk | Mitigation |
|---------|------|------------|
| REST API endpoints | High | Rate limiting, API keys in production |
| MQTT unauthenticated topics | High | TLS + username/password or client certs in production |
| Firmware upload | Medium | SHA256 verification, file type validation |
| Dashboard (browser) | Low | Content-Security-Policy headers |
| Prometheus /metrics | Low | Network restriction to monitoring VLAN |

---

## Security Measures by Component

### 1. FastAPI Backend

- **Input Validation** — All endpoints use Pydantic schemas (`schemas.py`) for request validation. Malformed payloads are rejected with 422 before reaching business logic.
- **SQL Injection** — SQLAlchemy ORM with parameterized queries. No raw SQL anywhere.
- **File Upload Safety** — Firmware uploads (`/ota/upload`) validate SHA256 hash server-side. `python-multipart` is used securely (no temporary file races).
- **Dependency Confusion** — All dependencies pinned to exact versions in `requirements.txt` with hashes verified during Docker build.
- **CORS** — In production, restrict origins to known dashboard domains. Not currently set (allows all in dev).

**Production recommendation:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dashboard.fleet-commander.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization"],
)
```

### 2. MQTT (Mosquitto Broker)

#### Development (Docker Compose)

- Anonymous access allowed for local dev only.
- Listener on 1883 (no TLS). **Never expose to the internet.**

#### Production

| Measure | Implementation |
|---------|---------------|
| TLS encryption | Add `listener 8883` with `cafile`, `certfile`, `keyfile` in `mosquitto.conf` |
| Authentication | Set `password_file` with per-device credentials |
| ACL | Use `acl_file` to restrict per-topic access per device |
| Rate limiting | `max_connections` and `max_client_id_len` |
| Bridge security | Authenticate bridge connections between MQTT clusters |

**Example production `mosquitto.conf` additions:**
```
listener 8883
cafile /etc/mosquitto/certs/ca.crt
certfile /etc/mosquitto/certs/server.crt
keyfile /etc/mosquitto/certs/server.key
require_certificate true
use_identity_as_username true

password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl

allow_anonymous false
max_connections 5000
```

**Example ACL (`acl`):**
```
# Device-001 can only pub/sub its own topics
user Device-001
topic write iot/fleet/Device-001/heartbeat
topic write iot/fleet/Device-001/status/+
topic read iot/fleet/Device-001/command/+

# Backend can publish to all command topics, subscribe to all status
user fleet-backend
topic write iot/fleet/+/command/+
topic read iot/fleet/+/status/+
topic read iot/fleet/register
```

### 3. OTA Pipeline Security

| Stage | Security Control |
|-------|-----------------|
| Firmware upload | SHA256 computed server-side, stored in DB. Re-computed on download. |
| OTA trigger | Device selection validated against authorized device list. |
| Command publish | Payload includes `{firmware_url, sha256_hash, timestamp}` — device verifies hash |
| Download delivery | Firmware served via HTTP from backend (`/firmware/{filename}`) — **use HTTPS in production** |
| Device verification | Device compares received SHA256 against expected. `hash_mismatch` triggers automatic rollback. |
| Rollback | Device firmware_version reverts to `previous_firmware_version`. Deployment marked `rolled_back`. |

### 4. Database

| Concern | Control |
|---------|---------|
| SQLite dev | File permissions: `chmod 600 fleet.db`. Not exposed to network. |
| PostgreSQL prod | Connection over TLS. Credentials in `.env` (not committed). |
| Secrets | `DATABASE_URL` uses `psycopg2` with SSL mode `require` in production. |
| Least privilege | DB user has only `INSERT/SELECT/UPDATE/DELETE` on fleet tables. No `DROP` or schema changes. |

### 5. Observability Stack

| Component | Security |
|-----------|----------|
| Prometheus | Bind to `127.0.0.1:9090` or monitoring VLAN only. No auth — use reverse proxy with basic auth. |
| Grafana | Default `admin/admin` — **must change immediately in production**. Enable OAuth or LDAP. |
| Metrics | `/metrics` exposes request latencies, OTA counts — no PII or secrets. Still, restrict to monitoring network. |

### 6. API Authentication (Production)

The current version uses **no authentication** for local development. For production, add one of:

**Option A — API Key (simplest):**
```python
from fastapi import Header, HTTPException

API_KEYS = os.environ.get("API_KEYS", "").split(",")

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

**Option B — JWT (recommended):**
```python
from fastapi import Depends, HTTPException
from jose import JWTError, jwt

async def get_current_user(token: str = Header(...)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401)
```

**Option C — mTLS (for device-to-backend):**
Require client certificates for MQTT (as shown above) and the REST API.

---

## Environment & Secrets Management

### .env File Rules

```bash
# NEVER commit .env to git (.env is in .gitignore)
# Use .env.example as a template with placeholder values

# Production secrets must use a secrets manager (HashiCorp Vault, AWS Secrets Manager, etc.)
# Example: injecting via Docker secrets:
echo "postgresql://fleet:${POSTGRES_PASSWORD}@postgres:5432/fleet" | docker secret create db_url -
```

### Docker Secrets

For Docker Swarm/Compose production:
```yaml
secrets:
  db_url:
    external: true
  mqtt_password:
    external: true

services:
  backend:
    secrets:
      - db_url
      - mqtt_password
```

---

## Network Security

### Port Exposure

| Service | Port | Dev | Prod |
|---------|------|-----|------|
| Backend (HTTP) | 8000 | ✅ localhost | 🔒 Internal LB only |
| Mosquitto (MQTT) | 1883 | ✅ localhost | 🔒 VPN/VPC only |
| Postgres | 5432 | ❌ Not exposed | 🔒 Private subnet |
| Prometheus | 9090 | ✅ localhost | 🔒 Monitoring subnet |
| Grafana | 3000 | ✅ localhost | 🔒 Reverse proxy + auth |

### Docker Network

```yaml
# All services communicate over a dedicated bridge network
networks:
  fleet-net:
    driver: bridge
    internal: false  # Set to true for prod if no external access needed

# Backend and DB should be on an internal network
services:
  backend:
    networks:
      - fleet-net
  postgres:
    networks:
      - fleet-net
    # No ports exposed to host
```

---

## Secure Development Lifecycle

### Pre-Commit

- [ ] No secrets committed (`git secrets` or `talisman` pre-commit hook)
- [ ] SQLAlchemy ORM used (no raw SQL)
- [ ] Input validation via Pydantic schemas
- [ ] All dependencies pinned and reviewed
- [ ] Docker images use slim base with `apt-get upgrade -y` at build time

### CI/CD Pipeline Checks

```yaml
# CI should run:
- safety check -r requirements.txt     # Known vulnerabilities
- bandit -r app/                        # Static security analysis
- pytest tests/                         # E2E tests pass
- docker scan fleet-management-backend  # Container image scan
```

**Dockerfile best practice:** Always include `apt-get update && apt-get upgrade -y` before `apt-get install` and clean up with `apt-get autoremove -y && rm -rf /var/lib/apt/lists/*` to apply base image security patches at build time.

### Dependency Auditing

```bash
# Check for known vulnerabilities in Python dependencies
pip install safety
safety check -r requirements.txt

# Check Docker base images for CVEs
docker scout quick fleet-management-backend
```

**Latest audit (May 2026):** 11 packages upgraded across 16 requirements to patch 10+ CVEs. All 3 Dockerfiles hardened with `apt-get upgrade -y` at build time. See [`AGENTS.md`](AGENTS.md) for the full change log.

### Update Cadence

| Dependency | Review Cadence |
|-----------|---------------|
| Python packages (requirements.txt) | Monthly |
| Docker base images | Monthly |
| Docker Compose images (mosquitto, postgres, prometheus, grafana) | Quarterly |
| TLS certificates | Before expiry + weekly monitoring |

---

## Compliance Considerations

For deployments requiring compliance (SOC 2, ISO 27001, etc.):

| Requirement | How Fleet Commander Addresses It |
|-------------|----------------------------------|
| Access control | API authentication layer (see above, not built-in but designed for extension) |
| Audit logging | All OTA state transitions logged; Prometheus metrics provide change history |
| Secure update mechanism | SHA256 verification + automatic rollback on mismatch |
| Least privilege | Per-device MQTT ACLs (production config); separate DB users |
| Data integrity | Firmware hash stored and verified at every stage |
| Incident response | OTA failure → automatic rollback; /metrics alerts via Alertmanager |

---

## Security Checklist for Production Deployment

- [ ] MQTT TLS enabled with client certificates
- [ ] MQTT `allow_anonymous false` with per-device ACLs
- [ ] PostgreSQL with TLS and strong password
- [ ] API authentication (JWT or API keys)
- [ ] CORS restricted to dashboard domain
- [ ] Grafana admin password changed from default
- [ ] Prometheus not exposed to public network
- [ ] Grafana behind reverse proxy with auth
- [ ] All `.env` secrets managed via secrets vault
- [ ] Docker images scanned for CVEs
- [ ] HTTPS enabled for backend (reverse proxy with cert)
- [ ] Firmware served over HTTPS (CDN with signed URLs)
- [ ] Rate limiting on `/devices/register` and `/ota/trigger`
- [ ] Logging and alerting configured for auth failures
- [ ] Regular dependency audit scheduled
